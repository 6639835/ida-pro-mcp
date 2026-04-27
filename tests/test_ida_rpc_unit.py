import json
import unittest
from unittest.mock import patch

from tests._mcp_spec_support import load_ida_rpc_module


class IdaRpcUnitTests(unittest.TestCase):
    def setUp(self):
        self.rpc = load_ida_rpc_module()
        self.rpc._output_cache.clear()
        self.rpc.MCP_UNSAFE.clear()
        self.rpc.MCP_EXTENSIONS.clear()

    def test_download_base_url_prefers_request_context_and_strips_slashes(self):
        self.rpc.set_download_base_url("http://local/")
        self.assertEqual(self.rpc.get_download_base_url(), "http://local")

        with patch.object(
            self.rpc, "get_current_request_external_base_url", return_value="https://public"
        ):
            self.assertEqual(self.rpc.get_download_base_url(), "https://public")

    def test_truncate_value_limits_strings_lists_dicts_and_depth(self):
        long_string = "x" * (self.rpc.OUTPUT_LIMIT_PREVIEW_STR_LEN + 1)
        truncated = self.rpc._truncate_value(
            {"items": list(range(20)), "long": long_string, "deep": {"a": {"b": {"c": {"d": {"e": {"f": "keep"}}}}}}}
        )

        self.assertEqual(truncated["items"], list(range(self.rpc.OUTPUT_LIMIT_PREVIEW_ITEMS)))
        self.assertIn("chars total", truncated["long"])
        self.assertEqual(truncated["deep"]["a"]["b"]["c"]["d"]["e"]["f"], "keep")
        self.assertEqual(self.rpc._truncate_value("same", depth=6), "same")

    def test_output_cache_evicts_oldest_entry(self):
        with patch.object(self.rpc, "OUTPUT_CACHE_MAX_SIZE", 2):
            self.rpc._cache_output("a", 1)
            self.rpc._cache_output("b", 2)
            self.rpc._cache_output("c", 3)
        self.assertIsNone(self.rpc.get_cached_output("a"))
        self.assertEqual(self.rpc.get_cached_output("b"), 2)
        self.assertEqual(self.rpc.get_cached_output("c"), 3)

    def test_tools_call_patch_handles_error_small_missing_and_large_results(self):
        responses = iter(
            [
                {"isError": True, "content": []},
                {"content": []},
                {"structuredContent": {"small": True}, "content": [], "isError": False},
                {
                    "structuredContent": {
                        "items": [{"name": str(i), "value": "x" * 2000} for i in range(20)]
                    },
                    "content": [],
                    "isError": False,
                },
            ]
        )

        def original(_name, _arguments=None, _meta=None):
            return next(responses)

        self.rpc.MCP_SERVER.registry.methods["tools/call"] = original
        self.rpc._install_tools_call_patch()
        call = self.rpc.MCP_SERVER.registry.methods["tools/call"]

        self.assertTrue(call("t")["isError"])
        self.assertEqual(call("t"), {"content": []})
        self.assertEqual(call("t")["structuredContent"], {"small": True})

        with (
            patch.object(self.rpc, "OUTPUT_LIMIT_MAX_CHARS", 100),
            patch.object(self.rpc, "_generate_output_id", return_value="out"),
        ):
            large = call("t")

        self.assertFalse(large["isError"])
        self.assertEqual(len(large["structuredContent"]["items"]), 10)
        self.assertEqual(self.rpc.get_cached_output("out")["items"][0]["name"], "0")
        self.assertEqual(large["_meta"]["ida_mcp"]["output_id"], "out")
        self.assertEqual(json.loads(large["content"][0]["text"])["items"][0]["name"], "0")

    def test_decorators_record_tools_resources_unsafe_and_extensions(self):
        def fn():
            return "ok"

        self.assertIs(self.rpc.unsafe(fn), fn)
        self.assertIn("fn", self.rpc.MCP_UNSAFE)

        self.assertIs(self.rpc.ext("dbg")(fn), fn)
        self.assertIn("fn", self.rpc.MCP_EXTENSIONS["dbg"])

        tool_fn = self.rpc.tool(fn)
        self.assertIs(tool_fn, fn)
        self.assertIn("fn", self.rpc.MCP_SERVER.tools.methods)

        self.assertIs(self.rpc.resource("test://x")(fn), fn)

    def test_current_transport_session_id_delegates_to_server(self):
        self.rpc.MCP_SERVER._transport_session_id.data = "http:abc"
        self.assertEqual(self.rpc.get_current_transport_session_id(), "http:abc")


if __name__ == "__main__":
    unittest.main()
