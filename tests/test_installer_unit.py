import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from ida_pro_mcp import installer


class InstallerConfigTests(unittest.TestCase):
    def test_python_executable_prefers_active_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = "Scripts" if sys.platform == "win32" else "bin"
            exe = "python.exe" if sys.platform == "win32" else "python3"
            path = os.path.join(tmp, bindir, exe)
            os.makedirs(os.path.dirname(path))
            open(path, "w", encoding="utf-8").close()

            with patch.dict(os.environ, {"VIRTUAL_ENV": tmp}, clear=False):
                self.assertEqual(installer.get_python_executable(), path)

    def test_copy_python_env_copies_only_set_python_vars(self):
        env = {}
        with patch.dict(os.environ, {"PYTHONPATH": "/tmp/lib"}, clear=True):
            self.assertTrue(installer.copy_python_env(env))
        self.assertEqual(env, {"PYTHONPATH": "/tmp/lib"})

        env = {}
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(installer.copy_python_env(env))
        self.assertEqual(env, {})

    def test_transport_url_helpers_normalize_and_classify(self):
        self.assertEqual(
            installer.normalize_transport_url("http://127.0.0.1:8744"),
            "http://127.0.0.1:8744/mcp",
        )
        self.assertEqual(
            installer.normalize_transport_url("http://127.0.0.1:8744/"),
            "http://127.0.0.1:8744/mcp",
        )
        self.assertEqual(
            installer.force_mcp_path("http://127.0.0.1:8744/sse"),
            "http://127.0.0.1:8744/mcp",
        )
        self.assertEqual(
            installer.infer_http_transport_type("http://127.0.0.1:8744/sse"), "sse"
        )
        self.assertEqual(
            installer.infer_http_transport_type("http://127.0.0.1:8744/mcp"), "http"
        )
        with self.assertRaises(Exception):
            installer.normalize_transport_url("not-a-url")

    def test_generate_mcp_config_handles_client_specific_shapes(self):
        with (
            patch.object(installer, "get_python_executable", return_value="/py"),
            patch.object(installer, "copy_python_env", return_value=False),
        ):
            self.assertEqual(
                installer.generate_mcp_config(client_name="Generic", transport="stdio"),
                {"command": "/py", "args": [installer.SERVER_SCRIPT]},
            )
            self.assertEqual(
                installer.generate_mcp_config(client_name="Opencode", transport="stdio"),
                {"type": "local", "command": ["/py", installer.SERVER_SCRIPT]},
            )

        with patch.object(installer, "copy_python_env", side_effect=lambda env: env.update({"PYTHONPATH": "x"}) or True):
            config = installer.generate_mcp_config(client_name="Generic", transport="stdio")
        self.assertEqual(config["env"], {"PYTHONPATH": "x"})

        installer.set_ida_rpc("10.1.2.3", 9000)
        self.assertEqual(
            installer.generate_mcp_config(client_name="Codex", transport="sse"),
            {"url": "http://10.1.2.3:9000/mcp"},
        )
        self.assertEqual(
            installer.generate_mcp_config(client_name="Claude", transport="sse"),
            {"type": "sse", "url": "http://10.1.2.3:9000/sse"},
        )
        self.assertEqual(
            installer.generate_mcp_config(
                client_name="Antigravity IDE", transport="http://h.example:1234/sse"
            ),
            {"type": "http", "serverUrl": "http://h.example:1234/mcp"},
        )
        self.assertEqual(
            installer.generate_mcp_config(
                client_name="Other", transport="http://h.example:1234/sse"
            ),
            {"type": "http", "url": "http://h.example:1234/mcp"},
        )
        installer.set_ida_rpc("127.0.0.1", 13337)

    def test_config_file_read_write_json_and_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "mcp.json")
            toml_path = os.path.join(tmp, "config.toml")

            installer._write_config_file(json_path, {"mcpServers": {"a": 1}}, is_toml=False)
            self.assertEqual(
                installer._read_config_file(json_path, is_toml=False),
                {"mcpServers": {"a": 1}},
            )

            installer._write_config_file(toml_path, {"mcp_servers": {"a": {"x": 1}}}, is_toml=True)
            self.assertEqual(
                installer._read_config_file(toml_path, is_toml=True),
                {"mcp_servers": {"a": {"x": 1}}},
            )

            with open(json_path, "w", encoding="utf-8") as f:
                f.write("{")
            self.assertIsNone(installer._read_config_file(json_path, is_toml=False))
            self.assertIsNone(installer._read_config_file(os.path.join(tmp, "missing.json"), is_toml=False))

    def test_mcp_servers_view_supports_special_json_and_toml(self):
        config = {}
        self.assertIs(
            installer._get_mcp_servers_view(
                config,
                client_name="Codex",
                is_toml=True,
                special_json_structures={},
            ),
            config["mcp_servers"],
        )

        config = {}
        view = installer._get_mcp_servers_view(
            config,
            client_name="VS Code",
            is_toml=False,
            special_json_structures={"VS Code": ("mcp", "servers")},
        )
        view["x"] = {}
        self.assertEqual(config, {"mcp": {"servers": {"x": {}}}})

        config = {}
        view = installer._get_mcp_servers_view(
            config,
            client_name="VS Code",
            is_toml=False,
            special_json_structures={"VS Code": (None, "servers")},
        )
        view["x"] = {}
        self.assertEqual(config, {"servers": {"x": {}}})

    def test_resolve_client_targets_accepts_aliases_and_skips_unknowns(self):
        configs = {"Claude": ("d", "f"), "Cursor": ("d2", "f2")}
        out = io.StringIO()
        with redirect_stdout(out):
            resolved = installer._resolve_client_targets(
                configs, ["claude-desktop", "cur", "missing", "Claude"]
            )
        self.assertEqual(list(resolved), ["Claude", "Cursor"])
        self.assertIn("Unknown client", out.getvalue())

    def test_is_client_installed_reads_global_and_special_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "settings.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"mcp": {"servers": {installer.MCP_SERVER_NAME: {}}}}, f)

            with patch.object(
                installer,
                "_get_scope_config_spec",
                return_value=({}, {"VS Code": ("mcp", "servers")}),
            ):
                self.assertTrue(installer.is_client_installed("VS Code", tmp, "settings.json"))

            self.assertFalse(installer.is_client_installed("VS Code", tmp, "missing.json"))
            with open(config_path, "w", encoding="utf-8") as f:
                f.write("{")
            self.assertFalse(installer.is_client_installed("VS Code", tmp, "settings.json"))

    def test_install_mcp_servers_installs_uninstalls_and_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = os.path.join(tmp, "cfg")
            os.makedirs(cfg_dir)
            cfg_path = os.path.join(cfg_dir, "mcp.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"github.com/mrexodia/ida-pro-mcp": {"old": True}}}, f)

            specs = {"Cursor": (cfg_dir, "mcp.json")}
            with (
                patch.object(installer, "_get_scope_config_spec", return_value=(specs, {})),
                patch.object(installer, "generate_mcp_config", return_value={"new": True}),
            ):
                installer.install_mcp_servers(only=["cursor"], quiet=True)
                with open(cfg_path, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["mcpServers"], {installer.MCP_SERVER_NAME: {"new": True}})

                installer.install_mcp_servers(uninstall=True, only=["Cursor"], quiet=True)
                with open(cfg_path, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["mcpServers"], {})

    def test_install_mcp_servers_creates_project_dirs_and_handles_invalid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = os.path.join(tmp, ".cursor")
            invalid_dir = os.path.join(tmp, "bad")
            os.makedirs(invalid_dir)
            with open(os.path.join(invalid_dir, "mcp.json"), "w", encoding="utf-8") as f:
                f.write("{")

            specs = {
                "Cursor": (missing_dir, "mcp.json"),
                "Bad": (invalid_dir, "mcp.json"),
            }
            with (
                patch.object(installer, "_get_scope_config_spec", return_value=(specs, {})),
                patch.object(installer, "generate_mcp_config", return_value={"ok": True}),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    installer.install_mcp_servers(project=True, quiet=False)

            self.assertTrue(os.path.exists(os.path.join(missing_dir, "mcp.json")))
            self.assertIn("invalid JSON", out.getvalue())

    def test_print_and_list_helpers_emit_expected_text(self):
        out = io.StringIO()
        with (
            patch.object(installer, "generate_mcp_config", return_value={"x": True}),
            redirect_stdout(out),
        ):
            installer.print_mcp_config()
        self.assertIn("STDIO MCP CONFIGURATION", out.getvalue())
        self.assertIn("STREAMABLE HTTP MCP CONFIGURATION", out.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            configs = {"Cursor": (tmp, "mcp.json"), "Other": (os.path.join(tmp, "missing"), "x.json")}
            out = io.StringIO()
            with patch.object(installer, "get_global_configs", return_value=configs), redirect_stdout(out):
                installer.list_available_clients()
        self.assertIn("Cursor", out.getvalue())
        self.assertIn("supports --project", out.getvalue())

        out = io.StringIO()
        with patch.object(installer, "get_global_configs", return_value={}), redirect_stdout(out):
            installer.list_available_clients()
        self.assertIn("Unsupported platform", out.getvalue())

    def test_install_mcp_servers_reports_unsupported_missing_and_not_installed(self):
        out = io.StringIO()
        with patch.object(installer, "_get_scope_config_spec", return_value=({}, {})), redirect_stdout(out):
            installer.install_mcp_servers()
        self.assertIn("Unsupported platform", out.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            specs = {"Cursor": (missing, "mcp.json")}
            out = io.StringIO()
            with (
                patch.object(installer, "_get_scope_config_spec", return_value=(specs, {})),
                patch.object(installer, "print_mcp_config") as print_config,
                redirect_stdout(out),
            ):
                installer.install_mcp_servers(project=False, quiet=False)
            self.assertIn("not found", out.getvalue())
            print_config.assert_called_once()

            os.makedirs(missing)
            out = io.StringIO()
            with (
                patch.object(installer, "_get_scope_config_spec", return_value=(specs, {})),
                redirect_stdout(out),
            ):
                installer.install_mcp_servers(uninstall=True, quiet=False)
            self.assertIn("not installed", out.getvalue())

    def test_scope_config_spec_and_install_selection_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(installer, "get_project_configs", return_value={"Cursor": (tmp, "mcp.json")}):
                configs, special = installer._get_scope_config_spec(project=True, project_dir=tmp)
            self.assertEqual(configs, {"Cursor": (tmp, "mcp.json")})
            self.assertIs(special, installer.PROJECT_SPECIAL_JSON_STRUCTURES)

        with patch.object(installer, "get_global_configs", return_value={"Cursor": ("d", "f")}):
            configs, special = installer._get_scope_config_spec(project=False)
        self.assertEqual(configs, {"Cursor": ("d", "f")})
        self.assertIs(special, installer.GLOBAL_SPECIAL_JSON_STRUCTURES)

        args = SimpleNamespace(transport="stdio", scope="global", allow_ida_free=True)
        self.assertEqual(installer._get_install_transport(uninstall=False, args=args, interactive=False), "stdio")
        self.assertEqual(installer._get_install_scope(args, interactive=True), "global")

        args = SimpleNamespace(transport=None, scope=None, allow_ida_free=True)
        with patch.object(installer, "interactive_choose", return_value=None):
            self.assertIsNone(installer._get_install_transport(uninstall=False, args=args, interactive=True))
            self.assertIsNone(installer._get_install_scope(args, interactive=True))
        with patch.object(installer, "interactive_choose", side_effect=["stdio", "Project (current directory)"]):
            self.assertEqual(installer._get_install_transport(uninstall=False, args=args, interactive=True), "stdio")
            self.assertEqual(installer._get_install_scope(args, interactive=True), "project")
        with patch.object(installer, "interactive_choose", side_effect=["SSE", "Global (user-level)"]):
            self.assertEqual(installer._get_install_transport(uninstall=False, args=args, interactive=True), "sse")
            self.assertEqual(installer._get_install_scope(args, interactive=True), "global")

    def test_interactive_install_cancel_empty_and_selected_paths(self):
        args = SimpleNamespace(transport=None, scope=None, allow_ida_free=True)
        out = io.StringIO()
        with patch.object(installer, "_get_install_transport", return_value=None), redirect_stdout(out):
            installer._interactive_install(uninstall=False, args=args)
        self.assertIn("Cancelled", out.getvalue())

        out = io.StringIO()
        with (
            patch.object(installer, "_get_install_transport", return_value="sse"),
            patch.object(installer, "_get_install_scope", return_value=None),
            redirect_stdout(out),
        ):
            installer._interactive_install(uninstall=False, args=args)
        self.assertIn("Cancelled", out.getvalue())

        out = io.StringIO()
        with (
            patch.object(installer, "_get_install_transport", return_value="sse"),
            patch.object(installer, "_get_install_scope", return_value="project"),
            patch.object(installer, "_get_scope_selection_items", return_value=[]),
            redirect_stdout(out),
        ):
            installer._interactive_install(uninstall=False, args=args)
        self.assertIn("Unsupported platform", out.getvalue())

        out = io.StringIO()
        with (
            patch.object(installer, "_get_install_transport", return_value="sse"),
            patch.object(installer, "_get_install_scope", return_value="project"),
            patch.object(installer, "_get_scope_selection_items", return_value=[("Cursor", False)]),
            patch.object(installer, "interactive_select", return_value=None),
            redirect_stdout(out),
        ):
            installer._interactive_install(uninstall=False, args=args)
        self.assertIn("Cancelled", out.getvalue())

        with (
            patch.object(installer, "_get_install_transport", return_value="sse"),
            patch.object(installer, "_get_install_scope", return_value="project"),
            patch.object(installer, "_get_scope_selection_items", return_value=[("Cursor", False)]),
            patch.object(installer, "interactive_select", return_value=["Cursor"]),
            patch.object(installer, "_apply_client_install") as apply_install,
        ):
            installer._interactive_install(uninstall=True, args=args)
        apply_install.assert_called_once_with(
            scope="project",
            transport="sse",
            uninstall=True,
            client_targets=["Cursor"],
        )


class InstallerPluginTests(unittest.TestCase):
    def test_remove_path_handles_files_dirs_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            installer._remove_path(missing)
            file_path = os.path.join(tmp, "file")
            open(file_path, "w", encoding="utf-8").close()
            installer._remove_path(file_path)
            self.assertFalse(os.path.lexists(file_path))

            dir_path = os.path.join(tmp, "dir")
            os.makedirs(dir_path)
            installer._remove_path(dir_path)
            self.assertFalse(os.path.lexists(dir_path))

    def test_install_link_or_copy_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            dst = os.path.join(tmp, "dst.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("x")

            self.assertTrue(installer._install_link_or_copy(src, dst))
            if os.path.islink(dst):
                self.assertFalse(installer._install_link_or_copy(os.path.realpath(src), dst))
            else:
                self.assertTrue(installer._install_link_or_copy(src, dst))

    def test_install_ida_plugin_install_uninstall_and_free_license_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(installer, "_get_ida_user_dir", return_value=tmp):
                installer.install_ida_plugin(quiet=True, allow_ida_free=True)
                self.assertTrue(installer.is_ida_plugin_installed())
                self.assertTrue(os.path.lexists(os.path.join(tmp, "plugins", "ida_mcp")))

                installer.install_ida_plugin(uninstall=True, quiet=True, allow_ida_free=True)
                self.assertFalse(installer.is_ida_plugin_installed())

                open(os.path.join(tmp, "idafree_test.hexlic"), "w", encoding="utf-8").close()
                with self.assertRaises(SystemExit):
                    installer.install_ida_plugin(quiet=True)

    def test_install_prompt_helpers_and_run_command(self):
        args = SimpleNamespace(transport=None, scope=None, allow_ida_free=True)
        self.assertEqual(installer._resolve_transport("stdio"), "stdio")
        self.assertEqual(installer._resolve_transport("sse"), "sse")
        self.assertEqual(installer._resolve_transport("http"), "streamable-http")
        self.assertEqual(installer._get_install_transport(uninstall=False, args=args, interactive=False), "streamable-http")
        self.assertEqual(installer._get_install_transport(uninstall=True, args=args, interactive=False), "stdio")
        self.assertEqual(installer._get_install_scope(args, interactive=False), "project")
        self.assertEqual(installer._parse_client_targets("cursor, ida-plugin, ,claude"), ["cursor", "claude"])

        calls = []
        with patch.object(installer, "install_mcp_servers", side_effect=lambda **kw: calls.append(kw)):
            installer._apply_client_install(
                scope="global",
                transport="sse",
                uninstall=False,
                client_targets=["Cursor"],
            )
        self.assertEqual(calls[0]["project"], False)

        run_calls = []
        with (
            patch.object(installer, "install_ida_plugin", side_effect=lambda **kw: run_calls.append(("ida", kw))),
            patch.object(installer, "_apply_client_install", side_effect=lambda **kw: run_calls.append(("client", kw))),
        ):
            installer.run_install_command(uninstall=False, targets_str="cursor", args=args)
        self.assertEqual(run_calls[0][0], "ida")
        self.assertEqual(run_calls[1][1]["client_targets"], ["cursor"])


if __name__ == "__main__":
    unittest.main()
