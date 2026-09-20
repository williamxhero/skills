import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from task_backend import TaskBackend, send_managed_turn


class FakeTransport:
    name = "fake"

    def send_message_to_thread(self, **params):
        return {"turn_id": "turn-2", "evidence": ["send:readback"]}

    def wait_for_turn_completion(self, thread_id, turn_id, timeout=None):
        return {"method": "turn/completed", "params": {"threadId": thread_id, "turnId": turn_id, "status": "completed"}}

    def read_history(self, thread_id, turn_id):
        return {"turns": [{"id": turn_id, "output": "verified"}], "evidence": ["history:readback"]}


class ManagedTurnBackendTests(unittest.TestCase):
    def test_send_retains_turn_waits_for_matching_completion_and_history(self):
        backend = TaskBackend("fake", FakeTransport())
        result = send_managed_turn(backend, "thread-1", "local", "continue", wait=True)
        self.assertEqual("turn-2", result["turn_id"])
        self.assertEqual("turn-2", result["completion"]["params"]["turnId"])
        self.assertIn("persisted_history", result["execution_evidence"])

    def test_notification_or_history_identity_mismatch_fails_closed(self):
        class Bad(FakeTransport):
            def wait_for_turn_completion(self, thread_id, turn_id, timeout=None):
                return {"method": "turn/completed", "params": {"threadId": thread_id, "turnId": "other", "status": "completed"}}
        with self.assertRaisesRegex(Exception, "identity mismatch"):
            send_managed_turn(TaskBackend("fake", Bad()), "thread-1", "local", "continue")

    def test_direct_app_server_uses_probed_thread_read_for_history(self):
        class Direct:
            def request(self, method, params):
                self.last = (method, params)
                return {"result": {"turns": [{"id": "turn-2"}], "evidence": ["thread/read"]}}
        direct = Direct()
        backend = TaskBackend("codex-app-server-jsonrpc", direct, {"read_thread": "thread/read"})
        from task_backend import read_persisted_history
        result = read_persisted_history(backend, "thread-1", "local", "turn-2")
        self.assertEqual("turn-2", result["turns"][0]["id"])
        self.assertEqual("thread/read", direct.last[0])


if __name__ == "__main__":
    unittest.main()
