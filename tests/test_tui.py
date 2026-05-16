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
from textual.widgets import Button, Input, ListView, RichLog

from textual.widgets import Button

from unittest.mock import patch

from signpdf_ui import core, paths
from signpdf_ui.tui import ConfirmScreen, PickGeometryScreen, SelectFilesScreen, SelectModeScreen, SignPdfUiApp


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


class TestSelectFilesScreen(unittest.IsolatedAsyncioTestCase):
    """Tests for the step-1 file picker."""

    async def test_file_list_highlight_updates_input(self):
        """Navigating the file list with ↓/↑ fills the pattern Input."""
        fake_files = [Path("/tmp/alpha.pdf"), Path("/tmp/beta.pdf"), Path("/tmp/gamma.pdf")]

        with patch("signpdf_ui.core.expand_pdf_patterns", return_value=fake_files):
            app = SignPdfUiApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await app.push_screen(SelectFilesScreen())
                await pilot.pause()
                await pilot.pause()

                lv = app.query_one("#file_list", ListView)
                inp = app.query_one("#pattern", Input)

                self.assertEqual(len(list(lv.children)), 3, "List should show all fake files")

                # Focus the list and navigate down — each move should update the Input
                lv.focus()
                await pilot.pause()
                before = inp.value

                await pilot.press("down")
                await pilot.pause()
                after = inp.value

                self.assertIn(
                    after,
                    [str(f) for f in fake_files],
                    "Input must contain a path from the file list after pressing ↓",
                )
                self.assertNotEqual(before, after, "Input should change when list highlight moves")


class TestSelectFilesScreenResume(unittest.IsolatedAsyncioTestCase):
    """Tests for SelectFilesScreen behaviour after returning from a deeper screen."""

    async def test_enter_after_back_from_step2_advances(self):
        """Going back from step 2 to step 1 and pressing Enter must advance to step 2 again.

        Bug: when the app starts with initial_files, all wizard screens are pushed
        synchronously in App.on_mount, so SelectFilesScreen was never the active
        screen.  lv.index could be None and focus could be wrong, causing Enter to
        silently do nothing or show an error instead of advancing.
        """
        fixture = paths.fixture_path("demo-form-with-sign-fields.pdf")
        app = SignPdfUiApp(initial_files=[fixture])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()
            # App opens directly on step 2
            self.assertIsInstance(app.screen, SelectModeScreen)

            # Navigate back to step 1
            await pilot.press("alt+left")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(app.screen, SelectFilesScreen)

            # Press Enter — must advance back to step 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            self.assertIsInstance(
                app.screen,
                SelectModeScreen,
                "Enter on SelectFilesScreen after returning from step 2 must push SelectModeScreen",
            )


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
