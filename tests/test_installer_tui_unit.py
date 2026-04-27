import io
import unittest
from unittest.mock import patch

from ida_pro_mcp import installer_tui


class InstallerTuiTests(unittest.TestCase):
    def test_tui_loop_confirms_cancels_and_ignores_noop(self):
        events = iter(["unknown", "enter"])
        renders = []

        def render():
            renders.append(True)
            return "title\nitem"

        def on_key(key):
            return "noop" if key == "unknown" else "confirm"

        stdout = io.StringIO()
        with patch.object(installer_tui.sys, "stdout", stdout):
            self.assertTrue(installer_tui._tui_loop(lambda: next(events), render, on_key))
        self.assertGreaterEqual(len(renders), 1)
        self.assertIn("\033[?25h", stdout.getvalue())

        stdout = io.StringIO()
        with patch.object(installer_tui.sys, "stdout", stdout):
            self.assertFalse(installer_tui._tui_loop(lambda: "esc", lambda: "x", lambda _key: "cancel"))

    def test_interactive_choose_moves_and_returns_selected_item(self):
        keys = iter(["down", "enter"])
        with patch.object(installer_tui, "_make_read_key", return_value=lambda: next(keys)):
            stdout = io.StringIO()
            with patch.object(installer_tui.sys, "stdout", stdout):
                self.assertEqual(
                    installer_tui.interactive_choose(["one", "two"], "Pick"), "two"
                )
        self.assertIn("Pick", stdout.getvalue())

    def test_interactive_choose_returns_none_without_tty_or_on_escape(self):
        with patch.object(installer_tui, "_make_read_key", return_value=None):
            self.assertIsNone(installer_tui.interactive_choose(["one"], "Pick"))

        with patch.object(installer_tui, "_make_read_key", return_value=lambda: "esc"):
            stdout = io.StringIO()
            with patch.object(installer_tui.sys, "stdout", stdout):
                self.assertIsNone(installer_tui.interactive_choose(["one"], "Pick"))

    def test_interactive_select_toggles_items_and_all_items(self):
        keys = iter(["space", "down", "a", "enter"])
        with patch.object(installer_tui, "_make_read_key", return_value=lambda: next(keys)):
            stdout = io.StringIO()
            with patch.object(installer_tui.sys, "stdout", stdout):
                self.assertEqual(
                    installer_tui.interactive_select(
                        [("Cursor", True), ("Claude", False)], "Targets"
                    ),
                    ["Cursor", "Claude"],
                )

        keys = iter(["space", "enter"])
        with patch.object(installer_tui, "_make_read_key", return_value=lambda: next(keys)):
            stdout = io.StringIO()
            with patch.object(installer_tui.sys, "stdout", stdout):
                self.assertEqual(
                    installer_tui.interactive_select([("Cursor", True)], "Targets"),
                    [],
                )
        self.assertIn("(none)", stdout.getvalue())

    def test_interactive_select_returns_none_without_read_key_or_escape(self):
        with patch.object(installer_tui, "_make_read_key", return_value=None):
            self.assertIsNone(installer_tui.interactive_select([("Cursor", False)], "Targets"))

        with patch.object(installer_tui, "_make_read_key", return_value=lambda: "esc"):
            stdout = io.StringIO()
            with patch.object(installer_tui.sys, "stdout", stdout):
                self.assertIsNone(installer_tui.interactive_select([("Cursor", False)], "Targets"))


if __name__ == "__main__":
    unittest.main()
