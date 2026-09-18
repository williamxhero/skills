import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app_server_bridge import AppServerBridge
from task_backend import BackendError, JsonRpcStdioTransport


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.archived = False

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"result": {"userAgent": "Codex Desktop"}}
        if method == "thread/read":
            return {"result": {"thread": self.thread("thread-1")}}
        if method == "thread/list":
            data = [self.thread("thread-1")] if params.get("archived") == self.archived else []
            return {"result": {"data": data, "nextCursor": None}}
        if method == "thread/start":
            return {"result": {"thread": self.thread("thread-2")}}
        return {"result": {}}

    @staticmethod
    def thread(thread_id):
        return {
            "id": thread_id, "sessionId": "session-1", "cwd": "C:/work",
            "projectId": "project-1", "model": "gpt-5.6-sol",
            "reasoningEffort": "high", "status": {"type": "idle"},
            "name": "[INN v=1 task=T1 run=R1 attempt=01 nonce=0123456789abcdef] task",
        }


class AppServerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.metadata = Path(self.directory.name) / "identity.json"
        self.write_metadata()
        self.rpc = FakeRpc()
        self.bridge = AppServerBridge([], self.metadata, transport=self.rpc)

    def tearDown(self):
        self.directory.cleanup()

    def write_metadata(self, **overrides):
        entry = {
            "formal_thread_id": "thread-1", "host_id": "local", "task_id": "T1",
            "run_id": "R1", "attempt_id": "01", "owner_id": "owner",
            "cwd": "C:/work", "project_id": "project-1",
        }
        entry.update(overrides)
        self.metadata.write_text(json.dumps({"threads": [entry]}), encoding="utf-8")

    def test_capabilities_and_readbacks_use_live_native_fields(self):
        capabilities = self.bridge.request("capabilities")
        self.assertEqual("thread-1", capabilities["probe_target"]["formal_thread_id"])
        identity = self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertEqual("T1", identity["task_id"])
        self.assertEqual("completed", identity["lifecycle"])
        route = self.bridge.request("read_applied_route", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertEqual({"model": "gpt-5.6-sol", "effort": "high"}, {key: route[key] for key in ("model", "effort")})
        self.assertTrue(any(method == "thread/read" for method, _ in self.rpc.calls))

    def test_identity_rejects_native_metadata_disagreement(self):
        self.write_metadata(cwd="C:/other")
        with self.assertRaisesRegex(BackendError, "cwd disagrees"):
            self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})

    def test_archive_readback_uses_archived_thread_listing(self):
        self.rpc.archived = True
        result = self.bridge.request("read_archive_state", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertTrue(result["archived"])
        self.assertIn(("thread/list", {"archived": True, "limit": 100}), self.rpc.calls)

    def test_create_persists_complete_identity_metadata(self):
        result = self.bridge.request("create_thread", {
            "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
            "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol",
        })
        self.assertEqual("thread-2", result["formal_thread_id"])
        entries = json.loads(self.metadata.read_text(encoding="utf-8"))["threads"]
        self.assertEqual("T2", next(entry for entry in entries if entry["formal_thread_id"] == "thread-2")["task_id"])

    def test_create_rejects_incomplete_controller_identity(self):
        with self.assertRaisesRegex(BackendError, "incomplete controller identity"):
            self.bridge.request("create_thread", {"cwd": "C:/work", "project_id": "project-1"})

    def test_existing_thread_with_unassigned_project_cannot_be_enrolled(self):
        self.rpc.thread = staticmethod(lambda thread_id: {
            "id": thread_id, "sessionId": "session-1", "cwd": "C:/work",
            "projectId": None, "model": "gpt-5.6-sol", "reasoningEffort": "high",
            "status": {"type": "idle"},
        })
        with self.assertRaisesRegex(BackendError, "projectId"):
            self.bridge.request("adopt_thread", {
                "formal_thread_id": "thread-1", "host_id": "local", "task_id": "T1",
                "run_id": "R1", "attempt_id": "01", "owner_id": "owner",
                "cwd": "C:/work", "project_id": "project-1",
            })

    def test_json_rpc_transport_skips_notifications_until_matching_response(self):
        class Process:
            def poll(self): return None

        class Input:
            def write(self, _): pass
            def flush(self): pass

        transport = JsonRpcStdioTransport.__new__(JsonRpcStdioTransport)
        transport.process = Process()
        transport.process.stdin = Input()
        transport.process.stdout = object()
        transport.timeout = 1
        import threading
        transport._lock = threading.Lock()
        transport._request_id = 0
        lines = iter([
            '{"method":"thread/started","params":{}}\n',
            '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n',
        ])
        with patch("task_backend._readline_with_timeout", side_effect=lambda *_: next(lines)):
            self.assertEqual({"ok": True}, transport.request("initialize", {})["result"])


if __name__ == "__main__":
    unittest.main()
