"""
Pure logic for signpdf-ui: command building, parsers, config loading, --init.

No TUI imports here, so this module is fully unit-testable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from . import paths
from .release import __version__


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class UiConfig:
    """In-memory representation of signpdf-ui.yml."""

    default_cert: Optional[Path]
    pyhanko_config: Path
    style_name: str
    editor: Optional[str]
    source_path: Path


def load_ui_config(config_path: Optional[Path] = None) -> UiConfig:
    """Load the UI config file. If config_path is None, the user config dir is used."""

    if config_path is None:
        config_path = paths.ui_config_path()

    if not config_path.exists():
        raise FileNotFoundError(
            f"UI config not found at {config_path}. Run `signpdf-ui --init` first."
        )

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    pyhanko_cfg_raw = raw.get("pyhanko_config", "pyhanko.yml")
    pyhanko_cfg = Path(os.path.expandvars(os.path.expanduser(pyhanko_cfg_raw)))
    if not pyhanko_cfg.is_absolute():
        pyhanko_cfg = (config_path.parent / pyhanko_cfg).resolve()

    default_cert_raw = raw.get("default_cert")
    default_cert: Optional[Path] = None
    if default_cert_raw:
        default_cert = Path(os.path.expandvars(os.path.expanduser(default_cert_raw)))

    return UiConfig(
        default_cert=default_cert,
        pyhanko_config=pyhanko_cfg,
        style_name=raw.get("style_name", "my-signature"),
        editor=raw.get("editor"),
        source_path=config_path,
    )


# ---------------------------------------------------------------------------
# --init
# ---------------------------------------------------------------------------


@dataclass
class InitResult:
    written: List[Path]
    skipped: List[Path]
    target_dir: Path


def init_config(force: bool = False, target_dir: Optional[Path] = None) -> InitResult:
    """Install bundled template files into the user config dir.

    Existing files are kept unless ``force`` is True.
    """

    if target_dir is None:
        target_dir = paths.user_config_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    skipped: List[Path] = []
    for filename in paths.TEMPLATE_FILENAMES:
        src = paths.template_path(filename)
        dst = target_dir / filename
        if dst.exists() and not force:
            skipped.append(dst)
            continue
        shutil.copy2(src, dst)
        written.append(dst)

    return InitResult(written=written, skipped=skipped, target_dir=target_dir)


# ---------------------------------------------------------------------------
# Signature command construction
# ---------------------------------------------------------------------------


@dataclass
class SignJob:
    """A single signing invocation."""

    input_file: Path
    output_file: Path
    field: str  # either an existing field name or "PAGE/X1,Y1,X2,Y2/NAME"
    cert_path: Path
    pyhanko_config: Path
    style_name: str

    def command(self) -> List[str]:
        return build_sign_command(
            input_file=self.input_file,
            output_file=self.output_file,
            field=self.field,
            cert_path=self.cert_path,
            pyhanko_config=self.pyhanko_config,
            style_name=self.style_name,
        )


def build_sign_command(
    *,
    input_file: Path,
    output_file: Path,
    field: str,
    cert_path: Path,
    pyhanko_config: Path,
    style_name: str,
) -> List[str]:
    """Build the pyhanko CLI invocation, mirroring the legacy bash script.

    Returns a list of strings suitable for subprocess.run without shell=True.
    """

    return [
        "pyhanko",
        "--config",
        str(pyhanko_config),
        "sign",
        "addsig",
        "--no-strict-syntax",
        "--style-name",
        style_name,
        "--field",
        field,
        "pkcs12",
        str(input_file),
        str(output_file),
        str(cert_path),
    ]


def run_sign_command(
    cmd: List[str],
    pyhanko_config: Path,
    *,
    stdin: Optional[str] = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a build_sign_command() result with the correct working directory.

    pyhanko resolves relative paths inside the YAML (e.g. ``background:
    watermark.png``) against its current working directory, so we always run
    from the directory containing the pyhanko config.
    """

    return subprocess.run(
        cmd,
        cwd=str(pyhanko_config.parent),
        input=stdin,
        text=stdin is not None,
        capture_output=capture_output,
    )


def output_path_for(input_file: Path) -> Path:
    """Translate INPUT.pdf -> INPUT_signed.pdf in the same directory."""

    stem = input_file.stem
    return input_file.with_name(f"{stem}_signed.pdf")


def is_already_signed_output(path: Path) -> bool:
    return path.name.endswith("_signed.pdf")


# ---------------------------------------------------------------------------
# pyhanko field listing
# ---------------------------------------------------------------------------


def list_fields(pdf_file: Path) -> List[str]:
    """Return signature field names from `pyhanko sign list FILE`.

    Each non-empty line of pyhanko's output is treated as a field name. If the
    output format changes in a future pyhanko release, this is the seam to fix.
    """

    proc = subprocess.run(
        ["pyhanko", "sign", "list", str(pdf_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_field_list(proc.stdout)


def parse_field_list(output: str) -> List[str]:
    """Parse the stdout of `pyhanko sign list`.

    pyhanko prints one line per field, formatted as ``NAME:STATUS`` (e.g.
    ``Person1:EMPTY``). We keep only the name part. Empty / comment lines are
    dropped so we are robust against future output decorations.
    """

    fields: List[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip ":STATUS" suffix if present.
        name = line.split(":", 1)[0].strip()
        if name:
            fields.append(name)
    return fields


# ---------------------------------------------------------------------------
# Rect extraction
# ---------------------------------------------------------------------------


# Matches `/Rect [x1 y1 x2 y2]` inside a PDF. Tokens may be ints or floats.
_RECT_RE = re.compile(
    rb"/Rect\s*\[\s*"
    rb"(-?\d+(?:\.\d+)?)\s+"
    rb"(-?\d+(?:\.\d+)?)\s+"
    rb"(-?\d+(?:\.\d+)?)\s+"
    rb"(-?\d+(?:\.\d+)?)\s*\]"
)


def extract_rects(pdf_file: Path) -> List[str]:
    """Extract rectangle bounding boxes from a PDF as `x1,y1,x2,y2` strings.

    Mirrors the behavior of the legacy `grep -a /Rect ... | awk ...` pipeline:
    coordinates are rounded to the nearest integer.
    """

    data = pdf_file.read_bytes()
    return parse_rects(data)


def parse_rects(data: bytes) -> List[str]:
    results: List[str] = []
    for match in _RECT_RE.finditer(data):
        rounded = [str(round(float(c))) for c in match.groups()]
        results.append(",".join(rounded))
    return results


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def expand_pdf_patterns(patterns: Iterable[str]) -> List[Path]:
    """Expand glob patterns and filter to existing .pdf files, skipping _signed outputs."""

    results: List[Path] = []
    seen: set = set()
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            candidates = sorted(Path().glob(pat))
        else:
            candidates = [Path(pat)]
        for c in candidates:
            if not c.is_file():
                continue
            if c.suffix.lower() != ".pdf":
                continue
            if is_already_signed_output(c):
                continue
            key = c.resolve()
            if key in seen:
                continue
            seen.add(key)
            results.append(c)
    return results


def version() -> str:
    return __version__
