from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "live_cwd_probe", ROOT / "validation/scripts/live_cwd_probe.py"
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class HistoryBridge:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def read_history(self, formal_thread_id, turn_id):
        self.calls.append((formal_thread_id, turn_id))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class LiveCwdProbeTests(unittest.TestCase):
    def test_live_probe_cleanup_requires_archive_readback(self):
        class Bridge:
            def __init__(self, archived):
                self.archived = archived
                self.calls = []

            def request(self, operation, params):
                self.calls.append((operation, params))
                if operation == "set_thread_archived":
                    return {"evidence": ["archive"]}
                return {"archived": self.archived, "evidence": ["archive/read"]}

        bridge = Bridge(True)
        _, readback = probe._archive_probe_thread(bridge, "thread-1", "local")
        self.assertTrue(readback["archived"])
        self.assertEqual(["set_thread_archived", "read_archive_state"],
                         [operation for operation, _ in bridge.calls])
        with self.assertRaisesRegex(probe.BackendError, "archive readback is incomplete"):
            probe._archive_probe_thread(Bridge(False), "thread-1", "local")

    def test_turn_output_is_extracted_from_protocol_items(self):
        turn = {"id": "turn-7", "items": [{"type": "agentMessage", "text": "V3_PROBE_READY"}]}
        self.assertEqual("V3_PROBE_READY", probe._turn_output(turn))

    def test_empty_first_history_is_retried_until_matching_turn_is_persisted(self):
        bridge = HistoryBridge([
            {"thread": {"turns": []}},
            {"thread": {"turns": [{"id": "turn-7", "status": "completed"}]}},
        ])
        result, attempts = probe._read_persisted_history(
            bridge, "thread-1", "turn-7", timeout=1, retry_interval=0,
            sleep_fn=lambda _: None,
        )
        self.assertEqual("turn-7", result["thread"]["turns"][0]["id"])
        self.assertEqual(2, len(attempts))
        self.assertEqual(2, len(bridge.calls))

    def test_rollout_empty_is_retried_but_unrelated_errors_fail_immediately(self):
        bridge = HistoryBridge([
            probe.BackendError("rollout abc is empty"),
            {"thread": {"turns": [{"id": "turn-7", "status": "completed"}]}},
        ])
        result, _ = probe._read_persisted_history(
            bridge, "thread-1", "turn-7", timeout=1, retry_interval=0,
            sleep_fn=lambda _: None,
        )
        self.assertEqual("turn-7", result["thread"]["turns"][0]["id"])

        failing = HistoryBridge([probe.BackendError("permission denied")])
        with self.assertRaisesRegex(probe.BackendError, "permission denied"):
            probe._read_persisted_history(
                failing, "thread-1", "turn-7", timeout=1, retry_interval=0,
                sleep_fn=lambda _: None,
            )

    def test_persistently_empty_history_fails_closed(self):
        bridge = HistoryBridge([{"thread": {"turns": []}}] * 3)
        with self.assertRaisesRegex(probe.BackendError, "history did not persist"):
            probe._read_persisted_history(
                bridge, "thread-1", "turn-7", timeout=0, retry_interval=0,
                sleep_fn=lambda _: None,
            )


if __name__ == "__main__":
    unittest.main()
