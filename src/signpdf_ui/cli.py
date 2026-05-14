"""
Command line entry point for signpdf-ui.

By default it launches the Textual TUI. The non-interactive flags
``--detect-fields`` and ``--extract-rects`` mirror the legacy bash script for
users who want quick one-shot operations or to call signpdf-ui from scripts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core
from .release import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signpdf-ui",
        description="TUI for signing PDFs with pyhanko.",
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


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.init:
        return _cmd_init(force=args.force)

    if args.detect_fields:
        return _cmd_detect_fields(args.detect_fields)

    if args.extract_rects:
        return _cmd_extract_rects(args.extract_rects)

    # Default: launch the TUI. Imported lazily so the non-interactive paths
    # don't pay the Textual import cost.
    from .tui import run_tui

    return run_tui()


if __name__ == "__main__":
    sys.exit(main())
