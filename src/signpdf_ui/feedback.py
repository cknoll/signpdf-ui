"""Feedback submission for signpdf-ui."""
from __future__ import annotations

import datetime
import json
import tempfile
import urllib.request
from pathlib import Path

from .release import __version__ as _version

FEEDBACK_URL = "https://api.tud.uber.space/feedback/signpdf-ui"
FEEDBACK_API_KEY = "HOOvtxoyMfRlK06MnyaztGufeldji7zvSsz6ZDLV9-g"


def send_feedback(message: str, email: str = "", version: str = "") -> bool:
    """Send feedback to the backend. Returns True on success, False on any error."""
    payload: dict = {"message": message}
    if email:
        payload["email"] = email
    if version:
        payload["version"] = version
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            FEEDBACK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": FEEDBACK_API_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def save_feedback_locally(message: str, email: str = "", version: str = "") -> Path:
    """Save feedback to a timestamped .txt file in cwd, falling back to tmpdir."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"signpdf-ui-feedback-message-{ts}.txt"
    lines = ["message:", message, ""]
    if email:
        lines += ["email:", email, ""]
    if version:
        lines += [f"version: {version}", ""]
    content = "\n".join(lines)
    for directory in (Path.cwd(), Path(tempfile.gettempdir())):
        try:
            path = directory / filename
            path.write_text(content, encoding="utf-8")
            return path
        except OSError:
            continue
    raise OSError("Could not write feedback to cwd or tmpdir")
