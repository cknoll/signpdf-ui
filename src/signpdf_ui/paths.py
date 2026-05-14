"""
Locations of configuration files and bundled templates.

Uses platformdirs so the same logic works on Linux, macOS and Windows.
"""

from pathlib import Path

import platformdirs

APP_NAME = "signpdf-ui"

UI_CONFIG_FILENAME = "signpdf-ui.yml"
PYHANKO_CONFIG_FILENAME = "pyhanko.yml"
WATERMARK_FILENAME = "watermark.png"

TEMPLATE_FILENAMES = (UI_CONFIG_FILENAME, PYHANKO_CONFIG_FILENAME, WATERMARK_FILENAME)


def user_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def ui_config_path() -> Path:
    return user_config_dir() / UI_CONFIG_FILENAME


def pyhanko_config_path() -> Path:
    return user_config_dir() / PYHANKO_CONFIG_FILENAME


def templates_dir() -> Path:
    return Path(__file__).parent / "templates"


def template_path(filename: str) -> Path:
    return templates_dir() / filename
