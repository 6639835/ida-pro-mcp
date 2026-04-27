import json
import os
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch


def load_discovery_module():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "ida_pro_mcp"
        / "ida_mcp"
        / "discovery.py"
    )
    spec = importlib.util.spec_from_file_location("_test_discovery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


discovery = load_discovery_module()


class DiscoveryTests(unittest.TestCase):
    def test_register_unregister_and_discover_sorted_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(discovery, "get_instances_dir", return_value=tmp):
                first = discovery.register_instance("127.0.0.1", 5001, os.getpid(), "b.bin", "b.idb")
                second = discovery.register_instance("127.0.0.1", 5000, os.getpid(), "a.bin", "a.idb")

                self.assertTrue(os.path.exists(first))
                self.assertTrue(os.path.exists(second))

                with (
                    patch.object(discovery, "is_pid_alive", return_value=True),
                    patch.object(discovery, "probe_instance", return_value=True),
                ):
                    result = discovery.discover_instances()

                self.assertEqual([item["binary"] for item in result], ["b.bin", "a.bin"])
                self.assertTrue(discovery.unregister_instance(5001))
                self.assertFalse(discovery.unregister_instance(5001))

    def test_discover_returns_empty_when_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            with patch.object(discovery, "get_instances_dir", return_value=missing):
                self.assertEqual(discovery.discover_instances(), [])

    def test_discover_cleans_invalid_missing_dead_and_unreachable_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid_json = os.path.join(tmp, "instance_1.json")
            missing_fields = os.path.join(tmp, "instance_2.json")
            dead_pid = os.path.join(tmp, "instance_3.json")
            unreachable = os.path.join(tmp, "instance_4.json")
            valid = os.path.join(tmp, "instance_5.json")

            with open(invalid_json, "w", encoding="utf-8") as f:
                f.write("{")
            for path, payload in (
                (missing_fields, {"host": "127.0.0.1"}),
                (dead_pid, {"host": "127.0.0.1", "port": 3, "pid": 3}),
                (unreachable, {"host": "127.0.0.1", "port": 4, "pid": 4}),
                (valid, {"host": "127.0.0.1", "port": 5, "pid": 5, "started_at": "z"}),
            ):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)

            def pid_alive(pid):
                return pid in {4, 5}

            def reachable(_host, port, timeout=2.0):
                return port == 5

            with (
                patch.object(discovery, "get_instances_dir", return_value=tmp),
                patch.object(discovery, "is_pid_alive", side_effect=pid_alive),
                patch.object(discovery, "probe_instance", side_effect=reachable),
            ):
                result = discovery.discover_instances()

            self.assertEqual([item["port"] for item in result], [5])
            for stale in (invalid_json, missing_fields, dead_pid, unreachable):
                self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(valid))

    def test_pid_liveness_and_probe_error_paths(self):
        self.assertTrue(discovery.is_pid_alive(os.getpid()))
        self.assertFalse(discovery.is_pid_alive(4_000_000))
        self.assertFalse(discovery.probe_instance("127.0.0.1", 1, timeout=0.01))

    def test_platform_specific_ida_user_dir(self):
        with patch.object(discovery.sys, "platform", "win32"), patch.dict(os.environ, {"APPDATA": r"C:\Users\u\AppData"}):
            self.assertTrue(discovery._get_ida_user_dir().endswith(os.path.join("Hex-Rays", "IDA Pro")))
        with patch.object(discovery.sys, "platform", "darwin"), patch.object(discovery.os.path, "expanduser", return_value="/home/u"):
            self.assertEqual(discovery._get_ida_user_dir(), "/home/u/.idapro")


if __name__ == "__main__":
    unittest.main()
