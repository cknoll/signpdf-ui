"""
Command line entry point for signpdf-ui.

By default it launches the interactive signing UI. The non-interactive flags
``--detect-fields`` and ``--extract-rects`` mirror the legacy bash script for
users who want quick one-shot operations or to call signpdf-ui from scripts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, paths
from .release import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signpdf-ui",
        description="Interactive signing UI for PDFs (powered by pyhanko).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"signpdf-ui {__version__}",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Copy bundled config templates (signpdf-ui.yml, pyhanko.yml, watermark.png) "
        "into the user config directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --init, overwrite existing config files.",
    )
    parser.add_argument(
        "--detect-fields",
        metavar="FILE",
        help="List signature fields in FILE (non-interactive, like the legacy bash script).",
    )
    parser.add_argument(
        "--extract-rects",
        metavar="FILE",
        help="Print rect bounding boxes found in FILE (non-interactive).",
    )
    parser.add_argument(
        "file",
        nargs="?",
        metavar="FILE",
        help="Open the signing UI with FILE pre-loaded (skips the file-selection step).",
    )
    parser.add_argument(
        "--multi",
        metavar="PATTERN",
        help='Open the signing UI with all PDFs matching PATTERN pre-loaded (e.g. "docs/*.pdf").',
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Copy bundled demo PDFs to /tmp/pdfsign-ui-demo-<timestamp>/ and print usage instructions.",
    )
    return parser


def _cmd_init(force: bool) -> int:
    result = core.init_config(force=force)
    print(f"Target directory: {result.target_dir}")
    for p in result.written:
        print(f"  wrote   {p.name}")
    for p in result.skipped:
        print(f"  skipped {p.name} (exists; use --force to overwrite)")
    return 0


def _cmd_detect_fields(filename: str) -> int:
    fields = core.list_fields(Path(filename))
    for name in fields:
        print(name)
    return 0


def _cmd_extract_rects(filename: str) -> int:
    for rect in core.extract_rects(Path(filename)):
        print(rect)
    return 0


def _cmd_demo() -> int:
    demo_dir = core.cmd_demo()
    print(f"Demo files copied to {demo_dir}/\n")
    for name in paths.FIXTURE_PDF_FILENAMES:
        print(f"  {demo_dir / name}")

    print(f"\nTo try signpdf-ui:\n")

    if paths.ui_config_path().exists():
        print(f"  1. Config already initialized — skip this step.")
    else:
        print(f"  1. Initialize config (once):")
        print(f"       signpdf-ui --init")
    print()

    print(f"  2. Open a demo file — pick the one that matches the workflow you want to try:\n")
    print(f"     a) PDF with predefined signature fields (use 'Existing signature field' mode):")
    print(f"          signpdf-ui {demo_dir / 'demo-form-with-sign-fields.pdf'}\n")
    print(f"     b) PDF with rect annotations already present (use 'Geometry' mode):")
    print(f"          signpdf-ui {demo_dir / 'demo-form-raw-with-rects.pdf'}\n")
    print(f"     c) PDF without rects — try the Okular workflow to draw and import a rect:")
    print(f"          signpdf-ui {demo_dir / 'demo-form-raw.pdf'}\n")

    print(f"The bundled demo certificate is used by default after --init.")
    print(f"Replace default_cert in ~/.config/signpdf-ui/signpdf-ui.yml with your own .p12.")
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.init:
        return _cmd_init(force=args.force)

    if args.detect_fields:
        return _cmd_detect_fields(args.detect_fields)

    if args.extract_rects:
        return _cmd_extract_rects(args.extract_rects)

    if args.demo:
        return _cmd_demo()

    # Resolve initial files from positional FILE or --multi PATTERN.
    initial_files = []
    if args.file:
        initial_files = core.expand_pdf_patterns([args.file])
        if not initial_files:
            print(f"No PDF file found: {args.file}", file=sys.stderr)
            return 1
    elif args.multi:
        initial_files = core.expand_pdf_patterns([args.multi])
        if not initial_files:
            print(f"No PDF files match: {args.multi}", file=sys.stderr)
            return 1

    # Default: launch the signing UI. Imported lazily so the non-interactive paths
    # don't pay the Textual import cost.
    from .tui import run_tui

    return run_tui(initial_files=initial_files or None)


if __name__ == "__main__":
    sys.exit(main())
