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
from fnmatch import fnmatch
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
from .release import __version__ as _version


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


def _is_demo_file(path: Path) -> bool:
    """True when path looks like a file copied by --demo (auto-fill password)."""
    return fnmatch(str(path), "/tmp/pdfsign-ui-demo-*/demo*.pdf")


# Lateral focus navigation reused by every screen/modal with side-by-side buttons.
# Added per-screen (not only on App) so it also works inside ModalScreen instances.
_LR = (
    Binding("left", "app.focus_previous", show=False),
    Binding("right", "app.focus_next", show=False),
)

# Back navigation for screens that have a Back action.
# alt+left mirrors browser back-navigation behaviour.
_BACK_LR = (
    Binding("escape", "app.pop_screen", "Back", show=False),
    Binding("alt+left", "app.pop_screen", "Back", show=False),
    Binding("left", "app.focus_previous", show=False),
    Binding("right", "app.focus_next", show=False),
)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


class MainMenu(Screen):
    BINDINGS = [*_LR]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Vertical(
            Static("Sign PDFs with a digital certificate (using pyhanko as backend)\n", id="title"),
            Button("Sign PDF(s)", id="sign", variant="primary"),
            Button("Edit config for user interface", id="edit_ui"),
            Button("Edit config for backend (pyhanko)", id="edit_pyhanko"),
            Button("Send feedback", id="feedback"),
            Button("Quit (Ctrl+q)", id="quit"),
            id="menu",
        )
        yield Footer()

    @on(Button.Pressed, "#sign")
    def _go_sign(self) -> None:
        self.app.push_screen(SelectFilesScreen())

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

    @on(Button.Pressed, "#feedback")
    def _feedback(self) -> None:
        pass  # placeholder — not yet implemented

    @on(Button.Pressed, "#quit")
    def _quit(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Generic message / confirm screen
# ---------------------------------------------------------------------------


class MessageScreen(Screen):
    BINDINGS = [*_BACK_LR]

    def __init__(self, *, title: str, text: str) -> None:
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(f"[b]{self._title}[/b]\n"),
            Static(self._text),
            Button("Back (Alt+←)", id="back"),
        )
        yield Footer()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()


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
    BINDINGS = [*_BACK_LR]

    def __init__(self, initial_pattern: str = "") -> None:
        super().__init__()
        self._initial_pattern = initial_pattern or "*.pdf"
        self._suppress_list_refresh: bool = False
        self._found_files: List[Path] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 1/4 — Select PDF(s)[/b]\n"),
            Static(f"Working directory: {Path.cwd()}\n"),
            Static("", id="found_label"),
            ListView(id="file_list"),
            Label("File name or pattern (e.g. report.pdf or *.pdf):"),
            Static("Hint: Use * as a wildcard to select multiple files at once.\n"),
            Input(value=self._initial_pattern, placeholder="*.pdf", id="pattern"),
            Horizontal(
                Button("Next  [↵ Enter]", id="next", variant="primary"),
                Button("Back (Alt+←)", id="back"),
            ),
            Static("", id="status"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_file_list(set_focus=True)

    def on_screen_resume(self) -> None:
        # Restore cursor and focus without re-scanning.  When all wizard screens
        # are pushed synchronously in App.on_mount, lv.index can be left at None
        # because ListView.extend() sets index=0 only if len(self)==1 at the
        # moment mount() is called — but that check races with the pending
        # AwaitRemove from a prior lv.clear().
        lv: ListView = self.query_one("#file_list", ListView)
        if self._found_files:
            if lv.index is None:
                lv.index = 0
            lv.focus()
        else:
            self.query_one("#pattern", Input).focus()

    def _refresh_file_list(self, set_focus: bool = False) -> None:
        pattern = self.query_one("#pattern", Input).value.strip() or "*.pdf"
        self._found_files = core.expand_pdf_patterns([pattern])
        n = len(self._found_files)
        word = "file" if n == 1 else "files"
        self.query_one("#found_label", Static).update(f"Found {n} PDF {word}:")
        lv: ListView = self.query_one("#file_list", ListView)
        lv.clear()
        for f in self._found_files:
            lv.append(ListItem(Label(str(f))))
        if set_focus:
            if self._found_files:
                lv.focus()
            else:
                self.query_one("#pattern", Input).focus()

    @on(Input.Changed, "#pattern")
    def _on_pattern_changed(self, _: Input.Changed) -> None:
        if self._suppress_list_refresh:
            self._suppress_list_refresh = False
            return
        self._refresh_file_list()

    @on(ListView.Highlighted, "#file_list")
    def _on_file_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        lv: ListView = self.query_one("#file_list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._found_files):
            self._suppress_list_refresh = True
            self.query_one("#pattern", Input).value = str(self._found_files[idx])

    @on(ListView.Selected, "#file_list")
    def _on_file_selected(self, _: ListView.Selected) -> None:
        self._next()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Input.Submitted, "#pattern")
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
    BINDINGS = [*_BACK_LR]

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
            Button("Use a signature field built into the PDF", id="mode_field", variant="primary"),
            Button("Place the signature in a custom area", id="mode_geom"),

            Button("Back (Alt+←)", id="back"),
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

    BINDINGS = [*_BACK_LR]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 3/4 — Pick a signature field[/b]\n"),
            Static("", id="hint"),
            ListView(id="fields"),
            Horizontal(
                Button("Sign at the selected field", id="use", variant="primary"),
                Button("Back (Alt+←)", id="back"),
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

    BINDINGS = [*_BACK_LR]

    def __init__(self) -> None:
        super().__init__()
        self._okular_temp: Optional[Path] = None
        self._original_rects: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[b]Step 3/4 — Signature placement[/b]\n"),
            Static("Format: PAGE/X1,Y1,X2,Y2/NAME — e.g. `1/189,578,356,615/X1`.\n"),
            Label("Manually specify field (or just use the default):"),
            Input(value="1/80,10,180,60/MY_CUSTOM_FIELD", id="field"),
            Button("Open copy in Okular to draw rect", id="okular_open"),
            Static("", id="rect_hint"),
            ListView(id="rects"),
            Horizontal(
                Button("Use this area  [↵ Enter]", id="use", variant="primary"),
                Button("Back (Alt+←)", id="back"),
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
            "Okular opened. Draw ONE rectangle annotation\n"
            "(1. Activate annotation toolbar from *Tools* menu or press (F6);\n"
            "2. Select Rectangle (hidden inside the Arrow-dropdown) or press Alt + 0)\n"
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

    @on(Input.Submitted, "#field")
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
    BINDINGS = [*_BACK_LR]

    def compose(self) -> ComposeResult:
        cfg = _load_config_or_none()
        default = str(cfg.default_cert) if cfg and cfg.default_cert else ""
        yield Header()
        yield Vertical(
            Static("[b]Step 4/4 — Certificate[/b]\n"),
            Label("Certificate file (.p12):"),
            Input(value=default, placeholder="/path/to/cert.p12", id="cert"),
            Horizontal(
                Button("Next  [↵ Enter]", id="next", variant="primary"),
                Button("Back (Alt+←)", id="back"),
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




class WrongPasswordModal(ModalScreen):
    """Shown when pyhanko rejects the PKCS#12 password."""

    BINDINGS = [*_LR]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[b]Wrong password![/b]\n", classes="warning-title"),
            Static("The certificate password was rejected.\nPlease check your password and try again."),
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

    def __init__(self, is_demo: bool = False) -> None:
        super().__init__()
        self._is_demo = is_demo

    def compose(self) -> ComposeResult:
        title = (
            "[b]Certificate password[/b]\n(demo file detected — password auto-filled)\n"
            if self._is_demo
            else "[b]Certificate password[/b]\n"
        )
        yield Vertical(
            Static(title),
            Input(password=True, placeholder="password", id="pw"),
            Horizontal(
                Button("Sign  [↵ Enter]", id="submit", variant="primary"),
                Button("Cancel", id="cancel"),
            ),
            Static("", id="status"),
            id="modal_inner",
        )

    def on_mount(self) -> None:
        pw = self.query_one("#pw", Input)
        if self._is_demo:
            pw.value = paths.FIXTURE_P12_PASSWORD
        pw.focus()

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
    """Shows a summary, the pyhanko command(s) inline, and triggers signing."""

    BINDINGS = [*_BACK_LR]

    def __init__(self) -> None:
        super().__init__()
        self._cmds: List[List[str]] = []

    def compose(self) -> ComposeResult:
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        cert_name = wiz.cert.name if wiz.cert else "(none)"
        if wiz.mode == "field":
            signing_method = "Signature field in PDF"
            location = wiz.field
        else:
            parts = wiz.field.split("/", 2)
            signing_method = "Custom placement"
            location = (
                f"page {parts[0]}, area {parts[1]} (label: {parts[2]})"
                if len(parts) == 3 else wiz.field
            )
        files_preview = "\n".join(f"  • {f.name} → {core.output_path_for(f).name}" for f in wiz.files[:10])
        if len(wiz.files) > 10:
            files_preview += f"\n  … and {len(wiz.files) - 10} more"
        n = len(wiz.files)
        cmd_hint = (
            "FYI: this command will be executed:"
            if n == 1
            else f"FYI: these {n} commands will be executed:"
        )
        yield Header()
        yield Vertical(
            Static("[b]Confirm[/b]\n"),
            Static(
                f"Signing method: {signing_method}\n"
                f"Location:       {location}\n"
                f"Certificate:    {cert_name}\n"
                f"Files ({n}):\n{files_preview}\n"
            ),
            Horizontal(
                Button("Sign", id="sign", variant="primary"),
                Button("Back (Alt+←)", id="back"),
            ),
            Static(cmd_hint, id="cmd_hint"),
            RichLog(id="cmd_box", highlight=False, markup=False),
            Button("Copy to clipboard", id="copy"),
            Static("", id="status"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#sign", Button).focus()
        cfg = _load_config_or_none()
        log: RichLog = self.query_one("#cmd_box", RichLog)
        if cfg is None:
            log.write("Config not found. Run `signpdf-ui --init` first.")
            self.query_one("#sign", Button).disabled = True
            self.query_one("#copy", Button).disabled = True
            log.styles.height = 3
            return
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        for f in wiz.files:
            cmd = core.build_sign_command(
                input_file=f,
                output_file=core.output_path_for(f),
                field=wiz.field,
                cert_path=wiz.cert,
                pyhanko_config=cfg.pyhanko_config,
                style_name=cfg.style_name,
            )
            self._cmds.append(cmd)
            log.write(shlex.join(cmd))
        log.styles.height = min(len(self._cmds), 5) + 3  # borders(2) + horizontal scrollbar(1)

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#copy")
    def _copy(self) -> None:
        if not self._cmds:
            return
        text = "\n".join(shlex.join(cmd) for cmd in self._cmds)
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

    @on(Button.Pressed, "#sign")
    def _sign(self) -> None:
        if not self._cmds:
            self.query_one("#status", Static).update("Config not found. Run `signpdf-ui --init` first.")
            return
        wiz: WizardState = self.app.wizard  # type: ignore[attr-defined]
        is_demo = bool(wiz.files) and _is_demo_file(wiz.files[0])
        self.app.push_screen(PasswordModal(is_demo=is_demo), callback=self._on_password)

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

    BINDINGS = [
        Binding("escape", "action_done", "Done", show=False),
        Binding("alt+left", "action_done", "Done", show=False),
        *_LR,
    ]

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
        # screen_stack[0] is Textual's blank base Screen; [1] is MainMenu.
        while len(self.app.screen_stack) > 2:
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
        Binding("escape", "app.pop_screen", "Close", show=False),
        Binding("f1", "app.pop_screen", "Close", show=False),
        Binding("alt+left", "app.pop_screen", "Close", show=False),
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
                "  Escape / Alt+←          — go back\n"
                "  F1                     — show this help\n"
                "  Ctrl+q                 — quit (from any screen)\n"
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
    TITLE = f"signpdf-ui v{_version}"
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
    SelectFilesScreen #file_list {
        height: 5;
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
    MainMenu #edit_ui {
        margin: 1 1 0 1;
    }
    MainMenu #feedback {
        margin: 1 1 0 1;
    }
    MainMenu #quit {
        margin: 1 1 0 1;
    }
    ConfirmScreen #cmd_hint {
        margin-top: 1;
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
        Binding("ctrl+q", "quit", "Quit", key_display="Ctrl+q"),
        Binding("tab", "focus_next", "Next field", key_display="Tab", priority=True),
        Binding("shift+tab", "focus_previous", "Prev field", key_display="Shift+Tab", priority=True),
        Binding("up", "focus_previous", show=False),
        Binding("down", "focus_next", show=False),
        Binding("left", "focus_previous", show=False),
        Binding("right", "focus_next", show=False),
        Binding("f1", "show_help", "Help", key_display="F1"),
    ]

    def __init__(self, initial_files: Optional[List[Path]] = None) -> None:
        super().__init__()
        self.wizard = WizardState()
        self._initial_files = initial_files or []

    def on_mount(self) -> None:
        self.push_screen(MainMenu())
        if self._initial_files:
            self.wizard.files = self._initial_files
            self.push_screen(SelectFilesScreen(initial_pattern=str(self._initial_files[0])))
            self.push_screen(SelectModeScreen())

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())


def run_tui(initial_files: Optional[List[Path]] = None) -> int:
    SignPdfUiApp(initial_files=initial_files).run()
    print()  # ensure terminal cursor is on a fresh line after TUI exits
    return 0
