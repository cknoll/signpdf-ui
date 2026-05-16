"""
Unit tests for TUI-specific behaviour.

TestPickGeometryWorker: static checks, no running app needed.
TestConfirmScreenLayout: headless Textual pilot tests for visual layout.
"""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.app import App
from textual.screen import Screen
from textual.widgets import RichLog

from textual.widgets import Button

from signpdf_ui import core, paths
from signpdf_ui.tui import ConfirmScreen, PickGeometryScreen, SignPdfUiApp


class TestPickGeometryWorker(unittest.TestCase):
    def test_screen_has_no_call_from_thread(self):
        """Baseline: call_from_thread is on App, not Screen."""
        self.assertTrue(hasattr(App, "call_from_thread"))
        self.assertFalse(hasattr(Screen, "call_from_thread"))

    def test_run_okular_uses_app_call_from_thread(self):
        """_run_okular must delegate to self.app.call_from_thread.

        Screen has no call_from_thread attribute; using self.call_from_thread
        inside a @work(thread=True) method raises AttributeError at runtime.
        """
        source = inspect.getsource(PickGeometryScreen._run_okular)
        self.assertNotIn(
            "self.call_from_thread",
            source,
            "Replace self.call_from_thread with self.app.call_from_thread "
            "(Screen inherits from Widget, not App).",
        )
        self.assertIn("self.app.call_from_thread", source)


def _make_app(cfg, n_files: int) -> SignPdfUiApp:
    """Return a SignPdfUiApp with wizard state pre-filled for n_files."""
    app = SignPdfUiApp()
    app.wizard.files = [paths.fixture_path("demo-form-with-sign-fields.pdf")] * n_files
    app.wizard.cert = paths.fixture_path("test_identity.p12")
    app.wizard.field = "Person1"
    app.wizard.mode = "field"
    return app


class TestMainMenuLayout(unittest.IsolatedAsyncioTestCase):
    """Headless pilot tests for the main menu layout."""

    async def test_all_buttons_same_width(self):
        """All buttons in the main menu must have the same width."""
        app = SignPdfUiApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            buttons = list(app.query("#menu Button").results(Button))
            self.assertGreater(len(buttons), 1, "Expected multiple buttons in #menu")
            widths = [b.size.width for b in buttons]
            self.assertEqual(
                len(set(widths)),
                1,
                f"Main menu buttons have different widths: "
                f"{[(b.id, w) for b, w in zip(buttons, widths)]}",
            )


class TestConfirmScreenLayout(unittest.IsolatedAsyncioTestCase):
    """Headless pilot tests that catch visual layout regressions."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        core.init_config(target_dir=tmp)
        self._cfg = core.load_ui_config(tmp / "signpdf-ui.yml")

    async def test_cmd_box_visible_for_single_file(self):
        """RichLog must have at least 1 visible content row for n=1.

        With a horizontal scrollbar present, the widget needs height >= 4:
          1 (top border) + 1 (content) + 1 (h-scrollbar) + 1 (bottom border).
        The bug was min(1,5)+2 = 3, leaving scrollable_content_region.height=0.
        """
        app = _make_app(self._cfg, n_files=1)
        with patch("signpdf_ui.tui._load_config_or_none", return_value=self._cfg):
            async with app.run_test(size=(120, 40)) as pilot:
                await app.push_screen(ConfirmScreen())
                await pilot.pause()
                await pilot.pause()
                log = app.query_one("#cmd_box", RichLog)
                visible_rows = log.scrollable_content_region.height
                self.assertGreater(
                    visible_rows,
                    0,
                    f"Command not visible: scrollable_content_region.height={visible_rows}, "
                    f"outer height={log.outer_size.height}. "
                    "Horizontal scrollbar is consuming the only content row.",
                )

    async def test_cmd_box_visible_for_multiple_files(self):
        """RichLog must show content for n=3 (below the cap of 5)."""
        app = _make_app(self._cfg, n_files=3)
        with patch("signpdf_ui.tui._load_config_or_none", return_value=self._cfg):
            async with app.run_test(size=(120, 40)) as pilot:
                await app.push_screen(ConfirmScreen())
                await pilot.pause()
                await pilot.pause()
                log = app.query_one("#cmd_box", RichLog)
                self.assertGreater(
                    log.scrollable_content_region.height,
                    0,
                    f"Commands not visible for n=3: "
                    f"scrollable_content_region.height={log.scrollable_content_region.height}",
                )


if __name__ == "__main__":
    unittest.main()
