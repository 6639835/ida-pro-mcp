import gzip
import http.server  # Preload stdlib http so local ida_mcp/http.py does not shadow it.
import io
import pathlib
import sys
import unittest
from types import SimpleNamespace
from typing import Any


_ZEROMCP_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "ida_pro_mcp"
    / "ida_mcp"
)
sys.path.insert(0, str(_ZEROMCP_SRC))
try:
    from zeromcp.jsonrpc import (
        JsonRpcRegistry,
        cancel_request,
        register_pending_request,
        unregister_pending_request,
    )
    from zeromcp.mcp import McpHttpRequestHandler
finally:
    sys.path.remove(str(_ZEROMCP_SRC))


class PendingRequestCancellationTests(unittest.TestCase):
    def test_same_request_id_is_isolated_by_transport_session(self):
        event_a = register_pending_request(1, "http:session-a")
        event_b = register_pending_request(1, "http:session-b")
        try:
            self.assertTrue(cancel_request(1, "http:session-a"))
            self.assertTrue(event_a.is_set())
            self.assertFalse(event_b.is_set())
        finally:
            unregister_pending_request(1, "http:session-a")
            unregister_pending_request(1, "http:session-b")


class JsonRpcValidationTests(unittest.TestCase):
    def test_union_that_includes_str_preserves_json_looking_string(self):
        registry = JsonRpcRegistry()

        @registry.method
        def echo(value: str | dict[str, Any]):
            return {"type": type(value).__name__, "value": value}

        response = registry.dispatch(
            {
                "jsonrpc": "2.0",
                "method": "echo",
                "params": {"value": '{"kind":"still-a-string"}'},
                "id": 1,
            }
        )

        self.assertIsNotNone(response)
        self.assertEqual(
            response["result"],
            {"type": "str", "value": '{"kind":"still-a-string"}'},
        )


class RequestBodyLimitTests(unittest.TestCase):
    def test_decompressed_body_must_respect_post_body_limit(self):
        body = b"x" * 1000
        compressed = gzip.compress(body)
        handler = object.__new__(McpHttpRequestHandler)
        handler.headers = {
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        }
        handler.rfile = io.BytesIO(compressed)
        handler.mcp_server = SimpleNamespace(post_body_limit=len(compressed) + 1)
        errors: list[tuple[int, str | None]] = []
        handler.send_error = lambda code, message=None, explain=None: errors.append(
            (code, message)
        )

        self.assertIsNone(handler._read_body())
        self.assertEqual(errors[0][0], 413)


if __name__ == "__main__":
    unittest.main()
