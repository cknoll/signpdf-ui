"""
Unit tests for TUI-specific behaviour that does not require a running Textual app.
"""

import inspect
import unittest

from textual.app import App
from textual.screen import Screen

from signpdf_ui.tui import PickGeometryScreen


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


if __name__ == "__main__":
    unittest.main()
