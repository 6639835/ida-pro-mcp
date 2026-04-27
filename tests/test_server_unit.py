import io
import json
import urllib.parse
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from ida_pro_mcp import server


class ServerProxyUnitTests(unittest.TestCase):
    def tearDown(self):
        server.IDA_HOST = "127.0.0.1"
        server.IDA_PORT = 13337
        server._output_proxy_targets.clear()
        server.mcp._enabled_extensions.data = set()
        server.mcp._transport_session_id.data = None

    def test_output_id_extraction_and_lru_target_cache(self):
        self.assertIsNone(server._extract_output_id({}))
        self.assertIsNone(server._extract_output_id({"result": []}))
        self.assertIsNone(server._extract_output_id({"result": {"_meta": {}}}))
        self.assertEqual(
            server._extract_output_id(
                {"result": {"_meta": {"ida_mcp": {"output_id": "abc"}}}}
            ),
            "abc",
        )

        with patch.object(server, "OUTPUT_PROXY_CACHE_MAX_SIZE", 2):
            server._remember_output_proxy_target("a", "h1", 1)
            server._remember_output_proxy_target("b", "h2", 2)
            self.assertEqual(server._get_output_proxy_target("a"), ("h1", 1))
            server._remember_output_proxy_target("c", "h3", 3)
            self.assertIsNone(server._get_output_proxy_target("b"))

    def test_proxy_request_path_and_headers_preserve_extensions_and_session(self):
        server.mcp._enabled_extensions.data = {"dbg", "types"}
        self.assertEqual(server._get_proxy_request_path(), "/mcp?ext=dbg,types")
        server.mcp._transport_session_id.data = "http:session-123"
        with patch.object(
            server, "get_current_request_external_base_url", return_value="https://example/base"
        ):
            headers = server._get_proxy_request_headers()
        self.assertEqual(headers["Mcp-Session-Id"], "session-123")
        self.assertEqual(headers[server.EXTERNAL_BASE_HEADER], "https://example/base")

        server.mcp._transport_session_id.data = "stdio:ignored"
        self.assertNotIn("Mcp-Session-Id", server._get_proxy_request_headers())

    def test_proxy_to_instance_accepts_dict_and_string_payloads(self):
        calls = []

        class Conn:
            def __init__(self, host, port, timeout=30):
                self.host = host
                self.port = port
                self.timeout = timeout

            def request(self, method, path, payload, headers):
                calls.append((method, path, payload, headers))

            def getresponse(self):
                return SimpleNamespace(
                    status=200,
                    reason="OK",
                    read=lambda: b'{"jsonrpc":"2.0","result":{"ok":true},"id":1}',
                )

            def close(self):
                calls.append(("closed", self.host, self.port))

        with patch.object(server.http.client, "HTTPConnection", Conn):
            self.assertEqual(server._proxy_to_instance("h", 1, {"x": 1})["result"], {"ok": True})
            self.assertEqual(server._proxy_to_instance("h", 1, "{}")["result"], {"ok": True})
        self.assertEqual(calls[0][2], '{"x": 1}')
        self.assertEqual(calls[2][2], b"{}")

    def test_call_ida_tool_success_and_error_paths(self):
        with patch.object(
            server,
            "_proxy_to_instance",
            return_value={"result": {"structuredContent": {"ok": True}}},
        ):
            self.assertEqual(server._call_ida_tool("h", 1, "tool", {}), {"ok": True})

        with patch.object(
            server,
            "_proxy_to_instance",
            return_value={"error": {"message": "rpc failed"}},
        ):
            with self.assertRaisesRegex(RuntimeError, "rpc failed"):
                server._call_ida_tool("h", 1, "tool", {})

        with patch.object(
            server,
            "_proxy_to_instance",
            return_value={
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "tool failed"}],
                }
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "tool failed"):
                server._call_ida_tool("h", 1, "tool", {})

    def test_dispatch_proxy_local_and_remote_paths(self):
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        local_tool = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_instances", "arguments": {}},
        }
        original_calls = []

        def fake_original(request):
            original_calls.append(request)
            return {"result": {"tools": [{"name": "list_instances"}]}, "id": 1}

        with patch.object(server, "dispatch_original", side_effect=fake_original):
            self.assertEqual(server.dispatch_proxy(initialize)["id"], 1)
            self.assertEqual(server.dispatch_proxy(notification)["id"], 1)
            self.assertEqual(server.dispatch_proxy(local_tool)["id"], 1)
        self.assertEqual(len(original_calls), 3)

        with patch.object(
            server, "_proxy_to_ida", return_value={"jsonrpc": "2.0", "result": "remote", "id": 3}
        ):
            self.assertEqual(
                server.dispatch_proxy(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "x"}))["result"],
                "remote",
            )

    def test_dispatch_tools_list_merges_ida_tools_and_ignores_ida_failure(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        local = {"result": {"tools": [{"name": "list_instances"}, {"name": "local"}]}}
        remote = {"result": {"tools": [{"name": "ida"}, {"name": "list_instances"}]}}

        with (
            patch.object(server, "dispatch_original", return_value=local),
            patch.object(server, "_proxy_to_ida", return_value=remote),
        ):
            result = server.dispatch_proxy(request)
        self.assertEqual([tool["name"] for tool in result["result"]["tools"]], ["ida", "list_instances", "local"])

        local = {"result": {"tools": [{"name": "local"}]}}
        with (
            patch.object(server, "dispatch_original", return_value=local),
            patch.object(server, "_proxy_to_ida", side_effect=RuntimeError("down")),
        ):
            self.assertEqual(server.dispatch_proxy(request), local)

    def test_dispatch_proxy_returns_none_for_failed_notification(self):
        request = {"jsonrpc": "2.0", "method": "tools/call", "params": {}}
        with patch.object(server, "_proxy_to_ida", side_effect=RuntimeError("down")):
            self.assertIsNone(server.dispatch_proxy(request))

    def test_local_tools_list_select_and_open_file(self):
        instances = [{"host": "127.0.0.1", "port": 9999, "pid": 1, "binary": "a", "idb_path": "a", "started_at": "t"}]
        with (
            patch.object(server, "discover_instances", return_value=instances),
            patch.object(server, "probe_instance", return_value=True),
        ):
            listed = server.list_instances()
        self.assertTrue(listed[0]["reachable"])
        self.assertFalse(listed[0]["active"])

        with patch.object(server, "set_ida_rpc") as set_rpc:
            self.assertTrue(server.select_instance(0)["success"])
            set_rpc.assert_called_with("127.0.0.1", 13337)

        with patch.object(server, "probe_instance", return_value=False):
            self.assertFalse(server.select_instance(9999)["success"])

        with (
            patch.object(server, "probe_instance", return_value=True),
            patch.object(server, "set_ida_rpc"),
        ):
            self.assertTrue(server.select_instance(9999, host="h")["success"])
            self.assertEqual((server.IDA_HOST, server.IDA_PORT), ("h", 9999))

        with (
            patch.object(server, "probe_instance", return_value=False),
            patch.object(server, "discover_instances", return_value=[]),
        ):
            self.assertFalse(server.open_file("/tmp/a")["success"])

        with (
            patch.object(server, "probe_instance", side_effect=[False, True, True]),
            patch.object(server, "discover_instances", return_value=instances),
            patch.object(server, "_call_ida_tool", return_value={"success": True}),
        ):
            self.assertEqual(server.open_file("/tmp/a"), {"success": True})

        with (
            patch.object(server, "probe_instance", return_value=True),
            patch.object(server, "_call_ida_tool", side_effect=RuntimeError("bad")),
        ):
            self.assertEqual(server.open_file("/tmp/a")["error"], "bad")

        with (
            patch.object(server, "probe_instance", return_value=True),
            patch.object(server, "_call_ida_tool", return_value="launched"),
        ):
            self.assertEqual(server.open_file("/tmp/a"), {"success": True, "result": "launched"})

    def test_proxy_http_handler_download_paths(self):
        class Handler(server.ProxyHttpRequestHandler):
            def __init__(self):
                pass

        handler = Handler.__new__(Handler)
        handler.path = "/output/12345678-1234-1234-1234-123456789abc.json"
        handler.wfile = io.BytesIO()
        handler.sent_headers = []
        handler.errors = []
        handler.responses = []
        handler._check_api_request = lambda: True
        handler.send_error = lambda code, message=None: handler.errors.append((code, message))
        handler.send_response = lambda status: handler.responses.append(status)
        handler.send_header = lambda key, value: handler.sent_headers.append((key, value))
        handler.send_cors_headers = lambda: handler.sent_headers.append(("cors", "ok"))
        handler.end_headers = lambda: handler.sent_headers.append(("end", ""))

        with patch.object(server, "_get_output_proxy_target", return_value=None):
            handler.do_GET()
        self.assertEqual(handler.errors, [(404, "Output not found or expired")])

        handler.errors.clear()
        with (
            patch.object(server, "_get_output_proxy_target", return_value=("h", 1)),
            patch.object(server, "_proxy_output_download", side_effect=RuntimeError("down")),
        ):
            handler.do_GET()
        self.assertEqual(handler.errors, [(502, "Failed to proxy output download: down")])

        handler.errors.clear()
        with (
            patch.object(server, "_get_output_proxy_target", return_value=("h", 1)),
            patch.object(
                server,
                "_proxy_output_download",
                return_value=(200, "OK", [("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")], b"{}"),
            ),
        ):
            handler.do_GET()
        self.assertEqual(handler.responses, [200])
        self.assertIn(("Content-Type", "application/json"), handler.sent_headers)
        self.assertNotIn(("Transfer-Encoding", "chunked"), handler.sent_headers)
        self.assertEqual(handler.wfile.getvalue(), b"{}")

        handler.path = "/not-output"
        with patch.object(server.McpHttpRequestHandler, "do_GET") as parent_get:
            handler.do_GET()
        parent_get.assert_called_once()

    def test_resolve_ida_rpc_explicit_and_discovery_paths(self):
        with patch.object(server, "set_ida_rpc") as set_rpc:
            server._resolve_ida_rpc(SimpleNamespace(ida_rpc="http://10.0.0.1:7000?ext=dbg,types"))
        self.assertEqual((server.IDA_HOST, server.IDA_PORT), ("10.0.0.1", 7000))
        self.assertEqual(server.mcp._enabled_extensions.data, {"dbg", "types"})
        set_rpc.assert_called_with("10.0.0.1", 7000)

        with self.assertRaises(Exception):
            server._resolve_ida_rpc(SimpleNamespace(ida_rpc="bad"))

        stderr = io.StringIO()
        with (
            patch.object(server, "discover_instances", return_value=[]),
            patch.object(server, "set_ida_rpc") as set_rpc,
            redirect_stderr(stderr),
        ):
            server._resolve_ida_rpc(SimpleNamespace(ida_rpc=None))
        self.assertIn("No IDA instances", stderr.getvalue())
        set_rpc.assert_called()

        inst = {"host": "h", "port": 8, "binary": "bin"}
        with (
            patch.object(server, "discover_instances", return_value=[inst]),
            patch.object(server, "set_ida_rpc"),
            redirect_stderr(io.StringIO()),
        ):
            server._resolve_ida_rpc(SimpleNamespace(ida_rpc=None))
        self.assertEqual((server.IDA_HOST, server.IDA_PORT), ("h", 8))

        instances = [
            {"host": "h1", "port": 1, "binary": "one"},
            {"host": "h2", "port": 2, "binary": "two"},
        ]
        stderr = io.StringIO()
        with (
            patch.object(server, "discover_instances", return_value=instances),
            patch.object(server, "set_ida_rpc"),
            redirect_stderr(stderr),
        ):
            server._resolve_ida_rpc(SimpleNamespace(ida_rpc=None))
        self.assertIn("Found 2 IDA instances", stderr.getvalue())
        self.assertEqual((server.IDA_HOST, server.IDA_PORT), ("h1", 1))

    def test_main_handles_validation_and_config_modes(self):
        argv = ["ida-pro-mcp", "--list-clients"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "list_available_clients") as list_clients,
        ):
            server.main()
        list_clients.assert_called_once()

        argv = ["ida-pro-mcp", "--scope", "project"]
        out = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            redirect_stdout(out),
        ):
            server.main()
        self.assertIn("--scope requires", out.getvalue())

        argv = ["ida-pro-mcp", "--config"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            patch.object(server, "print_mcp_config") as print_config,
        ):
            server.main()
        print_config.assert_called_once()

        argv = ["ida-pro-mcp", "--install", "cursor"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            patch.object(server, "run_install_command") as run_install,
        ):
            server.main()
        run_install.assert_called_once()

        argv = ["ida-pro-mcp", "--install", "--uninstall"]
        out = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            redirect_stdout(out),
        ):
            server.main()
        self.assertIn("Cannot install and uninstall", out.getvalue())

        argv = ["ida-pro-mcp"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            patch.object(server.mcp, "stdio", side_effect=KeyboardInterrupt),
        ):
            server.main()

        argv = ["ida-pro-mcp", "--transport", "http://127.0.0.1:9999/mcp"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
            patch.object(server.mcp, "serve") as serve,
            patch("builtins.input", side_effect=EOFError),
        ):
            server.main()
        serve.assert_called_once()

        argv = ["ida-pro-mcp", "--transport", "bad"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(server, "_resolve_ida_rpc"),
        ):
            with self.assertRaises(Exception):
                server.main()


if __name__ == "__main__":
    unittest.main()
