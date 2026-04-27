import os
import unittest
from unittest.mock import patch

from ida_pro_mcp import installer_data


class InstallerDataTests(unittest.TestCase):
    def test_get_project_configs_places_root_and_subdir_configs(self):
        result = installer_data.get_project_configs("/repo")
        self.assertEqual(result["Claude Code"], ("/repo", ".mcp.json"))
        self.assertEqual(result["Cursor"], (os.path.join("/repo", ".cursor"), "mcp.json"))

    def test_resolve_client_name_exact_alias_substring_and_ambiguous(self):
        available = ["Claude", "Claude Code", "Cursor", "VS Code"]
        self.assertEqual(installer_data.resolve_client_name("cursor", available), "Cursor")
        self.assertEqual(installer_data.resolve_client_name("claude-desktop", available), "Claude")
        self.assertEqual(installer_data.resolve_client_name("vs-code", available), "VS Code")
        self.assertEqual(installer_data.resolve_client_name("cur", available), "Cursor")
        self.assertEqual(installer_data.resolve_client_name("claude", available), "Claude")
        self.assertIsNone(installer_data.resolve_client_name("code", available))
        self.assertIsNone(installer_data.resolve_client_name("missing", available))

    def test_get_global_configs_for_supported_and_unsupported_platforms(self):
        with patch.object(installer_data.sys, "platform", "darwin"):
            self.assertIn("Claude", installer_data.get_global_configs())
            self.assertIn("BoltAI", installer_data.get_global_configs())

        with patch.object(installer_data.sys, "platform", "linux"):
            self.assertIn("Cursor", installer_data.get_global_configs())
            self.assertNotIn("Claude", installer_data.get_global_configs())

        with patch.object(installer_data.sys, "platform", "win32"), patch.dict(os.environ, {"APPDATA": r"C:\Users\u\AppData"}):
            configs = installer_data.get_global_configs()
            self.assertIn("VS Code", configs)
            self.assertIn("Claude", configs)

        with patch.object(installer_data.sys, "platform", "plan9"):
            self.assertEqual(installer_data.get_global_configs(), {})


if __name__ == "__main__":
    unittest.main()
