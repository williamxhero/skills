import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from app_server_bridge import AppServerBridge
from task_backend import BackendError, JsonRpcStdioTransport, probe_connector


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.archived = False
        self.turn_start_result = {"turn": {"id": "turn-1"}}
        self.notifications = []
        self.notifications_seen = []
        self.list_thread = self.thread

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"result": {"userAgent": "Codex Desktop"}}
        if method == "thread/read":
            return {"result": {"thread": self.thread("thread-1")}}
        if method == "thread/list":
            data = [self.list_thread("thread-1")] if params.get("archived", False) == self.archived else []
            return {"result": {"data": data, "nextCursor": None}}
        if method == "thread/start":
            return {"result": {"thread": self.thread("thread-2")}}
        if method == "thread/archive":
            self.archived = True
            return {"result": {}}
        if method == "turn/start":
            return {"result": self.turn_start_result}
        return {"result": {}}

    @staticmethod
    def thread(thread_id):
        return {
            "id": thread_id, "sessionId": "session-1", "cwd": "C:/work",
            "projectId": "project-1", "model": "gpt-5.6-sol",
            "reasoningEffort": "high", "status": {"type": "idle"},
            "name": "[INN v=1 task=T1 run=R1 attempt=01 nonce=0123456789abcdef] task",
        }

    def wait_for_notification(self, method, *, thread_id, turn_id, timeout):
        for notification in self.notifications:
            self.notifications_seen.append(notification)
            params = notification.get("params", {})
            if (notification.get("method") == method
                    and params.get("threadId") == thread_id
                    and params.get("turnId") == turn_id):
                return notification
        raise AssertionError("matching notification not found")


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

    def test_capabilities_skips_stale_first_metadata_entry(self):
        entries = [
            {
                "formal_thread_id": "stale-thread", "host_id": "local", "task_id": "STALE",
                "run_id": "R-stale", "attempt_id": "01", "owner_id": "owner",
                "cwd": "C:/work", "project_id": "project-1",
            },
            {
                "formal_thread_id": "thread-1", "host_id": "local", "task_id": "T1",
                "run_id": "R1", "attempt_id": "01", "owner_id": "owner",
                "cwd": "C:/work", "project_id": "project-1",
            },
        ]
        self.metadata.write_text(json.dumps({"threads": entries}), encoding="utf-8")
        self.rpc.list_thread = lambda thread_id: self.rpc.thread("thread-1")

        capabilities = self.bridge.request("capabilities")

        self.assertEqual("thread-1", capabilities["probe_target"]["formal_thread_id"])

    def test_list_tasks_drains_native_pages_before_returning_inventory(self):
        original_request = self.rpc.request
        calls = []

        def paged_request(method, params):
            calls.append((method, params))
            if method == "thread/list" and params.get("archived", False) is False:
                if params.get("cursor") is None:
                    return {"result": {"data": [self.rpc.thread("thread-1")], "nextCursor": "page-2"}}
                return {"result": {"data": [], "nextCursor": None}}
            return original_request(method, params)

        self.rpc.request = paged_request
        result = self.bridge.request("list_tasks", {})

        self.assertEqual(["thread-1"], [task["formal_thread_id"] for task in result["tasks"]])
        self.assertIsNone(result["next_cursor"])
        self.assertEqual("page-2", calls[2][1]["cursor"])

    def test_identity_rejects_native_metadata_disagreement(self):
        self.write_metadata(cwd="C:/other")
        with self.assertRaisesRegex(BackendError, "cwd disagrees"):
            self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})

    def test_archive_readback_uses_archived_thread_listing(self):
        self.rpc.archived = True
        result = self.bridge.request("read_archive_state", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertTrue(result["archived"])
        self.assertIn(("thread/list", {"archived": True, "limit": 100}), self.rpc.calls)

    def test_formal_target_inventory_falls_back_to_archived_thread_listing(self):
        self.rpc.archived = True

        result = self.bridge.request("list_tasks", {"formal_thread_id": "thread-1"})

        self.assertEqual(["thread-1"], [task["formal_thread_id"] for task in result["tasks"]])
        self.assertIn(("thread/list", {"archived": True, "limit": 100}), self.rpc.calls)

    def test_formal_target_inventory_drains_archived_pages(self):
        self.rpc.archived = True
        original_request = self.rpc.request

        def paged_request(method, params):
            self.rpc.calls.append((method, params))
            if method == "thread/list" and params.get("archived", False):
                if params.get("cursor") is None:
                    return {"result": {"data": [{"id": f"other-{i}"} for i in range(100)], "nextCursor": "page-2"}}
                return {"result": {"data": [self.rpc.thread("thread-1")], "nextCursor": None}}
            return original_request(method, params)

        self.rpc.request = paged_request

        result = self.bridge.request("list_tasks", {"formal_thread_id": "thread-1"})

        self.assertEqual(["thread-1"], [task["formal_thread_id"] for task in result["tasks"]])
        archived_calls = [params for method, params in self.rpc.calls if method == "thread/list" and params.get("archived", False)]
        self.assertEqual("page-2", archived_calls[1]["cursor"])

    def test_capability_probe_accepts_an_archived_formal_target(self):
        self.rpc.archived = True

        receipt = probe_connector(self.bridge)

        self.assertEqual("allow", receipt["decision"])
        self.assertEqual("thread-1", receipt["probe_target"]["formal_thread_id"])

    def test_create_persists_complete_identity_metadata(self):
        result = self.bridge.request("create_thread", {
            "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
            "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol", "effort": "high",
        })
        self.assertEqual("thread-2", result["formal_thread_id"])
        entries = json.loads(self.metadata.read_text(encoding="utf-8"))["threads"]
        self.assertEqual("T2", next(entry for entry in entries if entry["formal_thread_id"] == "thread-2")["task_id"])

    def test_create_with_saved_project_readback_omits_native_project_id(self):
        self.bridge.request("create_thread", {
            "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
            "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol", "effort": "high",
            "project_id_source": "saved_project_readback",
            "project_canonical_path": "C:/work",
            "project_identity_evidence": ["codex-app:list_projects:skills"],
        })
        start = next(params for method, params in self.rpc.calls if method == "thread/start")
        self.assertNotIn("projectId", start)
        self.assertEqual("high", start["config"]["model_reasoning_effort"])

    def test_create_rejects_invalid_saved_project_readback_before_start(self):
        with self.assertRaisesRegex(BackendError, "canonical path"):
            self.bridge.request("create_thread", {
                "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
                "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol",
                "project_id_source": "saved_project_readback",
                "project_canonical_path": "C:/other",
                "project_identity_evidence": ["codex-app:list_projects:skills"],
            })
        self.assertFalse(any(method == "thread/start" for method, _ in self.rpc.calls))

    def test_initialize_enables_project_aware_thread_creation(self):
        self.bridge.request("create_thread", {
            "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
            "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol",
        })
        initialize = next(params for method, params in self.rpc.calls if method == "initialize")
        self.assertTrue(initialize["capabilities"]["experimentalApi"])

    def test_create_rejects_incomplete_controller_identity(self):
        with self.assertRaisesRegex(BackendError, "incomplete controller identity"):
            self.bridge.request("create_thread", {"cwd": "C:/work", "project_id": "project-1"})

    def test_create_archives_thread_when_sidecar_persistence_fails(self):
        self.rpc.list_thread = staticmethod(lambda _: FakeRpc.thread("thread-2"))
        with patch.object(self.bridge, "_write_metadata", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(BackendError, "new thread archived"):
                self.bridge.request("create_thread", {
                    "task_id": "T2", "run_id": "R2", "attempt_id": "01", "owner_id": "owner",
                    "cwd": "C:/work", "project_id": "project-1", "model": "gpt-5.6-sol",
                })
        self.assertIn(("thread/archive", {"threadId": "thread-2"}), self.rpc.calls)
        self.assertIn(("thread/list", {"archived": True, "limit": 100}), self.rpc.calls)

    def test_project_id_missing_from_thread_read_is_cross_checked_from_thread_list(self):
        original = self.rpc.thread

        def thread_without_project(thread_id):
            value = original(thread_id)
            value.pop("projectId", None)
            return value

        self.rpc.thread = staticmethod(thread_without_project)
        result = self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertEqual("project-1", result["project_id"])
        self.assertIn(("thread/list", {"limit": 100}), self.rpc.calls)

    def test_project_id_conflict_between_read_and_list_is_rejected(self):
        original = self.rpc.thread

        def list_conflict(thread_id):
            value = original(thread_id)
            if thread_id == "thread-1":
                value["projectId"] = "other-project"
            return value

        self.rpc.thread = staticmethod(list_conflict)
        with self.assertRaisesRegex(BackendError, "projectId disagrees"):
            self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})

    def test_missing_native_project_id_uses_explicit_saved_project_readback(self):
        self.write_metadata(
            project_id_source="saved_project_readback",
            project_canonical_path="C:/work",
            project_identity_evidence=["projects:list:skills"],
        )
        original = self.rpc.thread

        def thread_without_project(thread_id):
            value = original(thread_id)
            value.pop("projectId", None)
            return value

        self.rpc.thread = staticmethod(thread_without_project)
        result = self.bridge.request("read_thread", {"formal_thread_id": "thread-1", "host_id": "local"})
        self.assertEqual("project-1", result["project_id"])
        self.assertNotIn(("thread/list", {"limit": 100}), self.rpc.calls)

    def test_existing_thread_with_unassigned_project_cannot_be_enrolled(self):
        self.rpc.thread = staticmethod(lambda thread_id: {
            "id": thread_id, "sessionId": "session-1", "cwd": "C:/work",
            "projectId": None, "model": "gpt-5.6-sol", "reasoningEffort": "high",
            "status": {"type": "idle"},
        })
        self.rpc.list_thread = self.rpc.thread
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


    def test_turn_start_preserves_turn_id_and_waits_for_matching_completion(self):
        self.rpc.turn_start_result = {"turn": {"id": "turn-7"}}
        self.rpc.notifications = [
            {"jsonrpc": "2.0", "method": "turn/completed",
             "params": {"threadId": "other", "turnId": "turn-7"}},
            {"jsonrpc": "2.0", "method": "turn/completed",
             "params": {"threadId": "thread-1", "turnId": "turn-7"}},
        ]
        result = self.bridge.request("send_message_to_thread", {
            "formal_thread_id": "thread-1", "host_id": "local", "message": "probe"
        })
        self.assertEqual("turn-7", result["turn_id"])
        completion = self.bridge.wait_for_turn_completion("thread-1", "turn-7", timeout=1)
        self.assertEqual("turn/completed", completion["method"])
        self.assertEqual(2, len(self.rpc.notifications_seen))

    def test_json_rpc_transport_retains_notifications_for_later_turn_wait(self):
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
            '{"jsonrpc":"2.0","method":"turn/completed","params":{"threadId":"thread-1","turnId":"turn-7"}}\n',
        ])
        with patch("task_backend._readline_with_timeout", side_effect=lambda *_: next(lines)):
            event = transport.wait_for_notification(
                "turn/completed", thread_id="thread-1", turn_id="turn-7", timeout=1
            )
        self.assertEqual("turn/completed", event["method"])
        self.assertEqual(1, len(transport.notifications))


if __name__ == "__main__":
    unittest.main()
