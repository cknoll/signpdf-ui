[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

# signpdf-ui

An interactive terminal UI (TUI) that wraps [pyhanko](https://github.com/MatthiasValvekens/pyHanko) so you can sign PDFs on Linux without memorizing flags.

## Install

```bash
pip install .
```

After installing, run the one-time setup once:

```bash
signpdf-ui --init
```

This copies the bundled configuration templates (`signpdf-ui.yml`, `pyhanko.yml`, `watermark.png`) into your user config directory — typically `~/.config/signpdf-ui/` on Linux. Use `--init --force` to overwrite existing files.


After this initialization you can run

```bash
signpdf-ui --demo
```

to copy bundled demo PDFs to `/tmp/pdfsign-ui-demo-<timestamp>/` and print usage instructions.

## Usage

### Interactive UI

Three ways to launch the wizard:

| Command | When to use |
| --- | --- |
| `signpdf-ui` | Files are in the working directory — pick them inside the wizard. |
| `signpdf-ui FILE.pdf` | You already know the file — opens directly at step 2 (mode selection). |
| `signpdf-ui "docs/*.pdf"` | Multiple files matching a glob — opens at step 2 with all matches pre-loaded. |

**`signpdf-ui`** (bare)

Launches the wizard at the main menu. In step 1 you type a file path or glob pattern (e.g. `*.pdf`) — the list updates live as you type. Selecting an entry or pressing Enter advances to step 2.

**`signpdf-ui FILE.pdf`**

Loads the given file immediately and skips to step 2 (mode selection). Pressing **Alt+←** at any point goes back to step 1, where the file path is already filled in.

**`signpdf-ui "docs/*.pdf"`**

Expands the glob and loads all matching PDFs as a batch; all will be signed with the same settings. Opens directly at step 2.

Main menu entries:

- **Sign PDF(s)** — walks you through four steps: file/pattern → mode (existing signature field or page+bounding box) → field/rect → certificate → confirmation. The confirmation screen shows the exact `pyhanko sign addsig ...` command(s) inline for full transparency, with a **Copy to clipboard** button.
- **Edit config for user interface** / **Edit config for backend (pyhanko)** — opens the respective YAML in `$VISUAL` / `$EDITOR` (or `xdg-open`).
- **Quit (Ctrl+q)** — exits (also available from any screen via the keyboard shortcut).

You are prompted for the PKCS#12 password via a modal dialog before signing. If the password is rejected a **Wrong password** modal appears with options to try again or go back.

### Defining the signature area visually with Okular

When you choose **Geometry (page + bounding box)** mode, the UI automatically extracts any existing rect annotations from the file and lists them for you to pick. If none are present (or you want a new one):

1. Click **"Open copy in Okular to draw rect"**.
   A temporary copy of the PDF opens in Okular — the original file is never touched.
2. In Okular, select the **Rectangle annotation tool** (toolbar ▭ button, or *Insert → Rectangle*).
3. Draw a rectangle over the desired signature area, **save** with **Ctrl+S**, then **close Okular**.
   The UI reads the temp file automatically and imports the single new rectangle.
4. Adjust the page number (first token) and field name (last token) of the pre-filled spec if needed, then proceed.

If you accidentally draw more than one rectangle the UI tells you and lets you try again.

> **Note:** Okular must be configured to embed annotations into the PDF file (the default when saving with Ctrl+S, as opposed to keeping a sidecar `.okular` file). The signature is applied to the *original* file; the temporary Okular copy is only used to capture the rectangle coordinates.

### Non-interactive CLI

The two utility flags from the legacy bash script are preserved for scripting:

```bash
signpdf-ui --detect-fields FILE.pdf
signpdf-ui --extract-rects FILE.pdf
```

For batch signing without the UI, use `pyhanko sign addsig` directly — the confirmation screen in the UI shows the exact invocation, which you can copy to the clipboard as a starting point.

## Configuration

`signpdf-ui.yml` lives next to `pyhanko.yml` in the user config dir. Keys:

| Key | Purpose |
| --- | --- |
| `default_cert` | Path to a `.p12` file used as the default in the cert picker. |
| `pyhanko_config` | Path to the pyhanko style YAML. Relative paths resolve against the directory of `signpdf-ui.yml`. |
| `style_name` | Stamp style name (must exist in `pyhanko.yml`). |
| `editor` | Optional editor command override for the "Edit config" buttons. |

The bundled `pyhanko.yml` defines a single stamp style `my-signature` with a watermark background. To change name, layout, watermark, etc., edit `pyhanko.yml` directly (see pyhanko's [stamp documentation](https://pyhanko.readthedocs.io/)).

## Development

```bash
pip install -e .
python -m pytest
```

Test layout:

- `tests/test_core.py` — unit tests for command building and the two parsers (field-list parser, rect extractor). These are the things most likely to break under future pyhanko or PDF format changes, so they get explicit coverage.
- `tests/test_e2e.py` — invokes the real `pyhanko sign addsig` against the bundled demo PDFs and the test certificate. Skipped if `pyhanko` is not on `PATH`.
- `tests/test_tui.py` — headless Textual pilot tests for visual layout and wizard navigation (uses `IsolatedAsyncioTestCase` + Textual's `run_test`).
- `tests/fixtures/` — demo PDFs + a self-signed test certificate (password `KXzolC-test-pw-s9Ckp7oZ`, not used anywhere else).
