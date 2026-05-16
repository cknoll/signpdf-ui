"""
Textual TUI for signpdf-ui.

The app is a small wizard: pick file(s) -> pick mode -> pick field/rect
-> pick certificate -> confirm (with "show command" toggle) -> per-file run
with password prompt. Two menu entries open the config files in $EDITOR.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from . import core, paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_in_editor(file_path: Path, editor_override: Optional[str] = None) -> None:
    """Suspend the TUI long enough to run $EDITOR (or fallback) on file_path."""

    editor = (
        editor_override
        or os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or "xdg-open"
    )
    subprocess.run([*shlex.split(editor), str(file_path)])


def _load_config_or_none():
    try:
        return core.load_ui_config()
    except FileNotFoundError:
        return None


# Lateral focus navigation reused by every screen/modal with side-by-side buttons.
# Added per-screen (not only on App) so it also works inside ModalScreen instances.
_LR = (
    Binding("left", "app.focus_previous", show=False),
    Binding("right", "app.focus_next", show=False),
)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


class MainMenu(Screen):
    BINDINGS = [Binding("q", "app.quit", "Quit"), *_LR]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Vertical(
            Static("signpdf-ui — sign PDFs through pyhanko\n", id="title"),
            Button("Sign PDF(s)", id="sign", variant="primary"),
            Button("Detect signature fields", id="detect"),
            Button("Extract rect coordinates", id="rects"),
            Button("Edit config (ui)", id="edit_ui"),
            Button("Edit config (pyhanko)", id="edit_pyhanko"),
            Button("Quit (q)", id="quit"),
            id="menu",
        )
        yield Footer()

    @on(Button.Pressed, "#sign")
    def _go_sign(self) -> None:
        self.app.push_screen(SelectFilesScreen())

    @on(Button.Pressed, "#detect")
    def _go_detect(self) -> None:
        self.app.push_screen(SingleFileActionScreen(mode="detect"))

    @on(Button.Pressed, "#rects")
    def _go_rects(self) -> None:
        self.app.push_screen(SingleFileActionScreen(mode="rects"))

    @on(Button.Pressed, "#edit_ui")
    def _edit_ui(self) -> None:
        self._edit(paths.ui_config_path())

    @on(Button.Pressed, "#edit_pyhanko")
    def _edit_pyhanko(self) -> None:
        self._edit(paths.pyhanko_config_path())

    def _edit(self, path: Path) -> None:
        if not path.exists():
            self.app.push_screen(
                MessageScreen(
                    title="Config missing",
                    text=(
                        f"{path} does not exist yet.\n"
                        "Run `signpdf-ui --init` once to create the default config files."
                    ),
                )
            )
            return
        cfg = _load_config_or_none()
        editor_override = cfg.editor if cfg else None
        with self.app.suspend():
            _open_in_editor(path, editor_override=editor_override)

    @on(Button.Pressed, "#quit")
    def _quit(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Generic message / confirm screen
# ---------------------------------------------------------------------------


class MessageScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def __init__(self, *, title: str, text: str) -> None:
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(f"[b]{self._title}[/b]\n"),
            Static(self._text),
            Button("Back", id="back"),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# "Single file action" — detect fields / extract rects
# ---------------------------------------------------------------------------


class SingleFileActionScreen(Screen):
    """Asks for one PDF, then runs either field detection or rect extraction."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def __init__(self, *, mode: str) -> None:
        super().__init__()
        assert mode in ("detect", "rects")
        self._mode = mode

    def compose(self) -> ComposeResult:
        action_label = "Detect signature fields" if self._mode == "detect" else "Extract rect coordinates"
        yield Header()
        yield Vertical(
            Static(f"[b]{action_label}[/b]\n"),
            Label("PDF file:"),
            Input(placeholder="path/to/file.pdf", id="path"),
            Horizontal(
                Button("Run", id="run", variant="primary"),
                Button("Back", id="back"),
            ),
            RichLog(id="output", highlight=False, markup=False),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#run")
    def _run(self) -> None:
        log: RichLog = self.query_one("#output", RichLog)
        log.clear()
        path_value = self.query_one("#path", Input).value.strip()
        if not path_value:
            log.write("No file given.")
            return
        path = Path(path_value).expanduser()
        if not path.is_file():
            log.write(f"Not a file: {path}")
            return
        try:
            if self._mode == "detect":
                results = core.list_fields(path)
                if not results:
                    log.write("(no signature fields found)")
                for name in results:
                    log.write(name)
            else:
                results = core.extract_rects(path)
                if not results:
                    log.write("(no rects found)")
                for rect in results:
                    log.write(rect)
        except subprocess.CalledProcessError as exc:
            log.write(f"pyhanko failed (exit {exc.returncode}):")
            log.write(exc.stderr or "(no stderr)")
        except Exception as exc:  # noqa: BLE001 — surface anything to the user
            log.write(f"Error: {exc}")


# ---------------------------------------------------------------------------
# Signing wizard — shared state on the App
# ---------------------------------------------------------------------------


class WizardState:
    def __init__(self) -> None:
        self.files: List[Path] = []
        self.mode: str = ""  # "field" or "geometry"
        self.field: str = ""  # final --field argument
        self.cert: Optional[Path] = None


class SelectFilesScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 1/4 — Select PDF(s)[/b]\n"),
            Static(f"Working directory: {Path.cwd()}\n"),
            Label("Glob pattern or path (e.g. `demo-*.pdf` or `./form.pdf`):"),
            Input(placeholder="*.pdf", id="pattern"),
            Horizontal(
                Button("Next", id="next", variant="primary"),
                Button("Back", id="back"),
            ),
            Static("", id="status"),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#next")
    def _next(self) -> None:
        pattern = self.query_one("#pattern", Input).value.strip()
        status: Static = self.query_one("#status", Static)
        if not pattern:
            status.update("Please enter a path or pattern.")
            return
        files = core.expand_pdf_patterns([pattern])
        if not files:
            status.update(f"No PDF files match: {pattern}")
            return
        self.app.wizard.files = files  # type: ignore[attr-defined]
        self.app.push_screen(SelectModeScreen())


class SelectModeScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def compose(self) -> ComposeResult:
        files: List[Path] = self.app.wizard.files  # type: ignore[attr-defined]
        preview_N = 5
        preview = "\n".join(f"  • {f}" for f in files[:preview_N])
        if len(files) > preview_N:
            preview += f"\n  … and {len(files) - preview_N} more"
        yield Header()
        yield Vertical(
            Static("[b]Step 2/4 — Choose signing mode[/b]\n"),
            Static(f"Files selected ({len(files)}):\n{preview}\n"),
            Button("Existing signature field (form)", id="mode_field", variant="primary"),
            Button("Geometry (page + bounding box)", id="mode_geom"),

            Button("Back", id="back"),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#mode_field")
    def _field(self) -> None:
        self.app.wizard.mode = "field"  # type: ignore[attr-defined]
        self.app.push_screen(PickFieldScreen())

    @on(Button.Pressed, "#mode_geom")
    def _geom(self) -> None:
        self.app.wizard.mode = "geometry"  # type: ignore[attr-defined]
        self.app.push_screen(PickGeometryScreen())


class PickFieldScreen(Screen):
    """Lists fields detected in the *first* selected file and lets the user pick one."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 3/4 — Pick a signature field[/b]\n"),
            Static("", id="hint"),
            ListView(id="fields"),
            Horizontal(
                Button("Use selected", id="use", variant="primary"),
                Button("Back", id="back"),
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        files: List[Path] = self.app.wizard.files  # type: ignore[attr-defined]
        first = files[0]
        hint: Static = self.query_one("#hint", Static)
        hint.update(f"Fields detected in: {first}")
        lv: ListView = self.query_one("#fields", ListView)
        try:
            for name in core.list_fields(first):
                lv.append(ListItem(Label(name)))
        except Exception as exc:  # noqa: BLE001
            hint.update(f"Could not list fields in {first}: {exc}")

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#use")
    def _use(self) -> None:
        lv: ListView = self.query_one("#fields", ListView)
        item = lv.highlighted_child
        if item is None:
            return
        label = item.query_one(Label)
        self.app.wizard.field = str(label.renderable)  # type: ignore[attr-defined]
        self.app.push_screen(PickCertScreen())


class PickGeometryScreen(Screen):
    """Geometry mode: pick a page, pick or enter a bbox, give the field a name."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def __init__(self) -> None:
        super().__init__()
        self._okular_temp: Optional[Path] = None
        self._original_rects: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 3/4 — Geometry[/b]\n"),
            Static("Format: PAGE/X1,Y1,X2,Y2/NAME — e.g. `1/189,578,356,615/X1`.\n"),
            Label("Manually specify field:"),
            Input(placeholder="1/189,578,356,615/X1", id="field"),
            Button("Open copy in Okular to draw rect", id="okular_open"),
            Static("", id="rect_hint"),
            ListView(id="rects"),
            Horizontal(
                Button("Use spec", id="use", variant="primary"),
                Button("Back", id="back"),
            ),
            Static("", id="status"),
        )
        yield Footer()

    def on_mount(self) -> None:
        files: List[Path] = self.app.wizard.files  # type: ignore[attr-defined]
        try:
            rects = core.extract_rects(files[0])
            self._original_rects = rects
            lv: ListView = self.query_one("#rects", ListView)
            for rect in rects:
                lv.append(ListItem(Label(rect)))

            # mention file name
            basename = files[0].name
            if rects:
                self.query_one("#rect_hint", Static).update(
                    f"{len(rects)} rect(s) found in {basename} — pick one:"
                )
            else:
                self.query_one("#rect_hint", Static).update(
                    f"No rects found {basename} — open Okular to draw one."
                )
        except Exception as exc:  # noqa: BLE001
            self.query_one("#status", Static).update(f"Error reading rects: {exc}")

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#okular_open")
    def _okular_open(self) -> None:
        files: List[Path] = self.app.wizard.files  # type: ignore[attr-defined]
        try:
            self._original_rects = core.extract_rects(files[0])
        except Exception:
            self._original_rects = []
        fd, tmp_str = tempfile.mkstemp(suffix=".pdf", prefix="signpdf-rect-")
        os.close(fd)
        self._okular_temp = Path(tmp_str)
        shutil.copy2(files[0], self._okular_temp)
        self.query_one("#status", Static).update(
            "Okular opened. Draw ONE rectangle annotation (toolbar ▭ or Insert > Rectangle),\n"
            "save (Ctrl+S), then close Okular — the rect will be imported automatically."
        )
        self._run_okular()

    @work(thread=True)
    def _run_okular(self) -> None:
        try:
            subprocess.run(
                ["okular", str(self._okular_temp)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            copy_rects = core.extract_rects(self._okular_temp)
            new_rects = [r for r in copy_rects if r not in self._original_rects]
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_status, f"Error: {exc}")
            return
        self.app.call_from_thread(self._handle_okular_result, new_rects)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    def _handle_okular_result(self, new_rects: List[str]) -> None:
        status: Static = self.query_one("#status", Static)
        if not new_rects:
            status.update(
                "No new rect found. Make sure you drew a rectangle annotation\n"
                "and saved (Ctrl+S) before closing Okular. Try again."
            )
            return
        if len(new_rects) > 1:
            status.update(
                f"{len(new_rects)} new rects found — please keep exactly one and try again."
            )
            return
        rect = new_rects[0]
        self.query_one("#field", Input).value = f"1/{rect}/X1"
        status.update(f"Imported: {rect}  (edit page number or field name above if needed)")
        self.query_one("#use", Button).focus()

    @on(ListView.Selected, "#rects")
    def _rect_selected(self, event: ListView.Selected) -> None:
        label = event.item.query_one(Label)
        rect = str(label.renderable)
        self.query_one("#field", Input).value = f"1/{rect}/X1"
        self.query_one("#use", Button).focus()

    @on(Button.Pressed, "#use")
    def _use(self) -> None:
        spec = self.query_one("#field", Input).value.strip()
        if spec.count("/") != 2:
            self.query_one("#status", Static).update(
                "Spec must be PAGE/X1,Y1,X2,Y2/NAME (two slashes)."
            )
            return
        self.app.wizard.field = spec  # type: ignore[attr-defined]
        self.app.push_screen(PickCertScreen())


class PickCertScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def compose(self) -> ComposeResult:
        cfg = _load_config_or_none()
        default = str(cfg.default_cert) if cfg and cfg.default_cert else ""
        yield Header()
        yield Vertical(
            Static("[b]Step 4/4 — Certificate[/b]\n"),
            Label("PKCS#12 certificate file (.p12):"),
            Input(value=default, placeholder="/path/to/cert.p12", id="cert"),
            Horizontal(
                Button("Next  [↵ Enter]", id="next", variant="primary"),
                Button("Back", id="back"),
            ),
            Static("", id="status"),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Input.Submitted, "#cert")
    def _cert_submitted(self, _: Input.Submitted) -> None:
        self._next()

    @on(Button.Pressed, "#next")
    def _next(self) -> None:
        value = self.query_one("#cert", Input).value.strip()
        if not value:
            self.query_one("#status", Static).update("Certificate path required.")
            return
        path = Path(value).expanduser()
        if not path.is_file():
            self.query_one("#status", Static).update(f"Not a file: {path}")
            return
        self.app.wizard.cert = path  # type: ignore[attr-defined]
        self.app.push_screen(ConfirmScreen())


class ShowCommandModal(ModalScreen):
    """Modal that shows the pyhanko command(s) with a clipboard copy button."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Close"), *_LR]

    def __init__(self, commands: List[List[str]]) -> None:
        super().__init__()
        self._commands = commands

    def compose(self) -> ComposeResult:
        text = "\n".join(shlex.join(cmd) for cmd in self._commands)
        yield Vertical(
            Static("[b]pyhanko command(s)[/b]\n"),
            Static(text, id="cmd_text"),
            Horizontal(
                Button("Copy to clipboard", id="copy"),
                Button("Close", id="close", variant="primary"),
            ),
            Static("", id="status"),
            id="modal_inner",
        )

    @on(Button.Pressed, "#copy")
    def _copy(self) -> None:
        text = "\n".join(shlex.join(cmd) for cmd in self._commands)
        # Try native clipboard tools before falling back to OSC 52 (not
        # supported by all terminals, e.g. Konsole on KDE).
        for args in (
            ["wl-copy"],                           # Wayland
            ["xclip", "-selection", "clipboard"],  # X11
            ["xsel", "--clipboard", "--input"],    # X11 alternative
        ):
            try:
                subprocess.run(args, input=text, text=True, check=True,
                               capture_output=True, timeout=2)
                self.query_one("#status", Static).update("Copied to clipboard.")
                return
            except (FileNotFoundError, subprocess.CalledProcessError,
                    subprocess.TimeoutExpired):
                continue
        self.app.copy_to_clipboard(text)  # OSC 52 fallback
        self.query_one("#status", Static).update("Copied to clipboard.")

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.app.pop_screen()


class WrongPasswordModal(ModalScreen):
    """Shown when pyhanko rejects the PKCS#12 password."""

    BINDINGS = [*_LR]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Wrong password![/b]\n", classes="warning-title"),
            Static("The PKCS#12 password was rejected.\nCheck your certificate password and try again."),
            Horizontal(
                Button("Try again", id="retry", variant="primary"),
                Button("Back", id="back"),
            ),
            id="modal_inner",
        )

    def on_mount(self) -> None:
        self.query_one("#retry", Button).focus()

    @on(Button.Pressed, "#retry")
    def _retry(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.dismiss(False)


class PasswordModal(ModalScreen):
    """Modal that prompts for the PKCS#12 password without suspending the UI."""

    BINDINGS = [*_LR]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]PKCS#12 password[/b]\n"),
            Input(password=True, placeholder="password", id="pw"),
            Horizontal(
                Button("Sign  [↵ Enter]", id="submit", variant="primary"),
                Button("Cancel", id="cancel"),
            ),
            Static("", id="status"),
            id="modal_inner",
        )

    def on_mount(self) -> None:
        self.query_one("#pw", Input).focus()

    @on(Input.Submitted, "#pw")
    def _enter(self, _event: Input.Submitted) -> None:
        self._submit()

    @on(Button.Pressed, "#submit")
    def _on_submit(self) -> None:
        self._submit()

    def _submit(self) -> None:
        pw = self.query_one("#pw", Input).value
        if not pw:
            self.query_one("#status", Static).update("Password must not be empty.")
            return
        self.dismiss(pw)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(Screen):
    """Shows a compact summary and lets the user inspect the command or sign."""

    BINDINGS = [Binding("escape", "app.pop_screen", "Back"), *_LR]

    def compose(self) -> ComposeResult:
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        cert_name = wiz.cert.name if wiz.cert else "(none)"
        mode_str = f"{wiz.mode} ({wiz.field})"
        files_preview = "\n".join(f"  • {f.name} → {core.output_path_for(f).name}" for f in wiz.files[:10])
        if len(wiz.files) > 10:
            files_preview += f"\n  … and {len(wiz.files) - 10} more"
        yield Header()
        yield Vertical(
            Static("[b]Confirm[/b]\n"),
            Static(
                f"Mode:  {mode_str}\n"
                f"Cert:  {cert_name}\n"
                f"Files ({len(wiz.files)}):\n{files_preview}\n"
            ),
            Horizontal(
                Button("Show command", id="show"),
                Button("Sign", id="sign", variant="primary"),
                Button("Back", id="back"),
            ),
            Static("", id="status"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sign", Button).focus()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#show")
    def _show(self) -> None:
        cfg = _load_config_or_none()
        if cfg is None:
            self.query_one("#status", Static).update("Config not found. Run `signpdf-ui --init` first.")
            return
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        cmds = [
            core.build_sign_command(
                input_file=f,
                output_file=core.output_path_for(f),
                field=wiz.field,
                cert_path=wiz.cert,
                pyhanko_config=cfg.pyhanko_config,
                style_name=cfg.style_name,
            )
            for f in wiz.files
        ]
        self.app.push_screen(ShowCommandModal(commands=cmds))

    @on(Button.Pressed, "#sign")
    def _sign(self) -> None:
        cfg = _load_config_or_none()
        if cfg is None:
            self.query_one("#status", Static).update("Config not found. Run `signpdf-ui --init` first.")
            return
        self.app.push_screen(PasswordModal(), callback=self._on_password)

    def _on_password(self, password: Optional[str]) -> None:
        if password is None:
            return
        cfg = _load_config_or_none()
        if cfg is None:
            return
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        results = []
        for f in wiz.files:
            out = core.output_path_for(f)
            cmd = core.build_sign_command(
                input_file=f,
                output_file=out,
                field=wiz.field,
                cert_path=wiz.cert,
                pyhanko_config=cfg.pyhanko_config,
                style_name=cfg.style_name,
            )
            proc = core.run_sign_command(
                cmd,
                pyhanko_config=cfg.pyhanko_config,
                stdin=f"{password}\n",
                capture_output=True,
                start_new_session=True,  # detaches /dev/tty so getpass reads stdin
            )
            results.append((f, out, proc.returncode, proc.stderr or ""))
        if all(rc != 0 for _, _, rc, _ in results) and any(
            core.is_wrong_password_error(stderr) for _, _, _, stderr in results
        ):
            self.app.push_screen(WrongPasswordModal(), callback=self._on_wrong_password)
            return
        self.app.push_screen(SignResultScreen(results=results))

    def _on_wrong_password(self, retry: bool) -> None:
        if retry:
            self.app.push_screen(PasswordModal(), callback=self._on_password)


class SignResultScreen(Screen):
    """Shows per-file signing results with optional Okular open buttons."""

    BINDINGS = [Binding("escape", "action_done", "Done"), *_LR]

    def __init__(self, results: List) -> None:
        super().__init__()
        self._results = results  # List of (in_path, out_path, returncode, stderr)
        self._success_outputs: List[Path] = [
            out for _, out, rc, _ in results if rc == 0 and out.is_file()
        ]

    def compose(self) -> ComposeResult:
        lines = []
        for in_f, out_f, rc, stderr in self._results:
            icon = "✓" if rc == 0 else "✗"
            lines.append(f"  {icon} {in_f.name} → {out_f.name}")
            if rc != 0:
                for line in (stderr or "").strip().splitlines()[:4]:
                    lines.append(f"      {line}")
        yield Header()
        yield Vertical(
            Static("[b]Signing results[/b]\n"),
            Static("\n".join(lines) + "\n"),
            *[
                Button(f"Open {out_f.name} in Okular", id=f"okular_{i}")
                for i, out_f in enumerate(self._success_outputs)
            ],
            Button("Done (back to menu)", id="done", variant="primary"),
        )
        yield Footer()

    @on(Button.Pressed, "#done")
    def action_done(self) -> None:
        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    @on(Button.Pressed)
    def _any_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("okular_"):
            idx = int(bid.split("_", 1)[1])
            subprocess.Popen(
                ["okular", str(self._success_outputs[idx])],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------


class HelpScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Close"),
        Binding("f1", "app.pop_screen", "Close"),
        *_LR,
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Keyboard shortcuts[/b]\n"),
            Static(
                "  Tab / Shift+Tab        — move between fields\n"
                "  Up / Down              — same as Tab / Shift+Tab\n"
                "  Left / Right           — switch between side-by-side buttons\n"
                "  Enter / Space          — activate focused button\n"
                "  Escape                 — go back\n"
                "  F1                     — show this help\n"
                "  q                      — quit (from main menu)\n"
            ),
            Button("Close", id="close"),
        )
        yield Footer()

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class SignPdfUiApp(App):
    COMMANDS = frozenset()
    ENABLE_COMMAND_PALETTE = False  # removes "^p palette" from the footer

    CSS = """
    Screen {
        align: center middle;
    }
    Vertical {
        width: 80%;
        max-width: 100;
        padding: 1 2;
    }
    Button {
        margin: 0 1;
    }
    Vertical > Button {
        width: 100%;
    }
    Input {
        margin-bottom: 1;
    }
    ListView {
        height: 10;
        border: solid $primary;
    }
    RichLog {
        height: 12;
        border: solid $accent;
    }
    ModalScreen {
        align: center middle;
    }
    #modal_inner {
        width: 80%;
        max-width: 100;
        padding: 1 2;
        background: $surface;
        border: solid $primary;
    }
    .warning-title {
        color: $error;
    }
    WrongPasswordModal #modal_inner {
        border: solid $error;
    }
    WrongPasswordModal Horizontal {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "app.quit", "Quit", show=False),
        Binding("up", "focus_previous", show=False),
        Binding("down", "focus_next", show=False),
        Binding("left", "focus_previous", show=False),
        Binding("right", "focus_next", show=False),
        Binding("f1", "show_help", "F1 Help"),
    ]

    def __init__(self, initial_files: Optional[List[Path]] = None) -> None:
        super().__init__()
        self.wizard = WizardState()
        self._initial_files = initial_files or []

    def on_mount(self) -> None:
        self.push_screen(MainMenu())
        if self._initial_files:
            self.wizard.files = self._initial_files
            self.push_screen(SelectModeScreen())

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())


def run_tui(initial_files: Optional[List[Path]] = None) -> int:
    SignPdfUiApp(initial_files=initial_files).run()
    print()  # ensure terminal cursor is on a fresh line after TUI exits
    return 0
