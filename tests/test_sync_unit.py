import importlib.util
import pathlib
import sys
import threading
import types
import unittest
from unittest.mock import patch


def load_sync_module():
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "ida_pro_mcp" / "ida_mcp"
    package_name = "_sync_test_ida_mcp"
    module_name = package_name + ".sync"

    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]

    idaapi = types.ModuleType("idaapi")
    idaapi.MFF_WRITE = 1
    idaapi.get_kernel_version = lambda: "9.2"
    idaapi.execute_sync = lambda func, _flags: func()

    idc = types.ModuleType("idc")
    batch_values = []

    def batch(value):
        batch_values.append(value)
        return 0

    idc.batch = batch

    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]

    rpc = types.ModuleType(package_name + ".rpc")

    class McpToolError(Exception):
        pass

    rpc.McpToolError = McpToolError

    zeromcp = types.ModuleType(package_name + ".zeromcp")
    zeromcp.__path__ = [str(root / "zeromcp")]
    jsonrpc = types.ModuleType(package_name + ".zeromcp.jsonrpc")
    cancel_event = {"value": None}

    class RequestCancelledError(Exception):
        pass

    jsonrpc.RequestCancelledError = RequestCancelledError
    jsonrpc.get_current_cancel_event = lambda: cancel_event["value"]

    old_modules = {
        name: sys.modules.get(name)
        for name in (
            "idaapi",
            "idc",
            package_name,
            package_name + ".rpc",
            package_name + ".zeromcp",
            package_name + ".zeromcp.jsonrpc",
        )
    }
    sys.modules.update(
        {
            "idaapi": idaapi,
            "idc": idc,
            package_name: package,
            package_name + ".rpc": rpc,
            package_name + ".zeromcp": zeromcp,
            package_name + ".zeromcp.jsonrpc": jsonrpc,
        }
    )

    spec = importlib.util.spec_from_file_location(module_name, root / "sync.py")
    sync = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = sync
    spec.loader.exec_module(sync)
    sync._test_batch_values = batch_values
    sync._test_cancel_event = cancel_event
    sync._test_old_modules = old_modules
    return sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.sync = load_sync_module()

    def tearDown(self):
        for name, module in self.sync._test_old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_sync_wrapper_returns_values_and_restores_batch_mode(self):
        self.assertEqual(self.sync.sync_wrapper(lambda: "ok", timeout_override=0), "ok")
        self.assertEqual(self.sync._test_batch_values, [1, 0])

    def test_sync_wrapper_reraises_function_exceptions(self):
        def fail():
            raise ValueError("bad")

        with self.assertRaisesRegex(ValueError, "bad"):
            self.sync.sync_wrapper(fail, timeout_override=0)

    def test_sync_wrapper_rejects_reentrant_call_stack(self):
        self.sync.call_stack.put("outer")
        try:
            with self.assertRaises(self.sync.IDASyncError):
                self.sync.sync_wrapper(lambda: None, timeout_override=0)
        finally:
            while not self.sync.call_stack.empty():
                self.sync.call_stack.get()

    def test_timeout_and_cancel_paths(self):
        ticks = iter([0.0, 2.0])
        with patch.object(self.sync.time, "monotonic", side_effect=lambda: next(ticks)):
            with self.assertRaisesRegex(self.sync.IDASyncError, "timed out"):
                self.sync.sync_wrapper(lambda: sum(range(3)), timeout_override=1.0)

        event = threading.Event()
        event.set()
        self.sync._test_cancel_event["value"] = event
        with self.assertRaises(self.sync.CancelledError):
            self.sync.sync_wrapper(lambda: sum(range(3)), timeout_override=0)

    def test_idasync_and_tool_timeout(self):
        @self.sync.idasync
        @self.sync.tool_timeout(0)
        def add(a, b):
            return a + b

        self.assertEqual(add(2, 3), 5)

    def test_timeout_env_parsing_and_normalization(self):
        with patch.dict(self.sync.os.environ, {self.sync._TOOL_TIMEOUT_ENV: "2.5"}):
            self.assertEqual(self.sync._get_tool_timeout_seconds(), 2.5)
        with patch.dict(self.sync.os.environ, {self.sync._TOOL_TIMEOUT_ENV: "x"}):
            self.assertEqual(
                self.sync._get_tool_timeout_seconds(),
                self.sync._DEFAULT_TOOL_TIMEOUT_SEC,
            )
        self.assertEqual(self.sync._normalize_timeout("3"), 3.0)
        self.assertIsNone(self.sync._normalize_timeout("x"))
        self.assertEqual(self.sync.IDAError("message").message, "message")

    def test_is_window_active_handles_no_qt_app_and_active_window(self):
        widgets = types.SimpleNamespace()
        qt = types.ModuleType("PySide6")
        qt.QtWidgets = widgets
        sys.modules["PySide6"] = qt
        try:
            widgets.QApplication = types.SimpleNamespace(instance=lambda: None)
            self.assertFalse(self.sync.is_window_active())

            app = types.SimpleNamespace(activeWindow=lambda: object())
            widgets.QApplication = types.SimpleNamespace(instance=lambda: app)
            self.assertTrue(self.sync.is_window_active())
        finally:
            sys.modules.pop("PySide6", None)


if __name__ == "__main__":
    unittest.main()
