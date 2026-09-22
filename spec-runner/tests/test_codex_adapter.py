from __future__ import annotations

import types
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.codex_adapter import CodexAdapter
from spec_runner.errors import RunnerError


class FakeResult:
    id = "turn-123"
    status = types.SimpleNamespace(value="completed")
    error = None
    final_response = "structured worker output"
    items = ["message"]
    started_at = 1
    completed_at = 2


class FakeThread:
    id = "thread-123"

    def run(self, prompt: str, **kwargs: object) -> FakeResult:
        self.prompt = prompt
        self.run_kwargs = kwargs
        return FakeResult()


class FakeTurn:
    id = "turn-started-123"

    def run(self) -> FakeResult:
        return FakeResult()


class FakeThreadWithTurn(FakeThread):
    def turn(self, prompt: str, **kwargs: object) -> FakeTurn:
        self.prompt = prompt
        self.turn_kwargs = kwargs
        return FakeTurn()


class FakeCodex:
    def __init__(self, config: object):
        self.config = config
        self.thread = FakeThread()
        self.start_kwargs: dict[str, object] | None = None

    def __enter__(self) -> "FakeCodex":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def thread_start(self, **kwargs: object) -> FakeThread:
        self.start_kwargs = kwargs
        return self.thread


class CodexAdapterTests(unittest.TestCase):
    def test_uses_published_thread_start_and_preserves_formal_ids(self) -> None:
        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            holder["codex"] = FakeCodex(config)
            return holder["codex"]

        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
        )
        result = CodexAdapter(codex_factory=factory, sdk_module=sdk).run(
            prompt="do the bounded task",
            repository_path=Path("C:/repo"),
            model="gpt-test",
            effort="high",
        )
        self.assertEqual(result.thread_id, "thread-123")
        self.assertEqual(result.turn_id, "turn-123")
        self.assertEqual(result.status, "completed")
        self.assertEqual(holder["codex"].start_kwargs["model"], "gpt-test")
        self.assertEqual(holder["codex"].thread.run_kwargs["effort"], "high")

    def test_published_turn_boundary_reports_identity_before_result(self) -> None:
        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            codex = FakeCodex(config)
            codex.thread = FakeThreadWithTurn()
            holder["codex"] = codex
            return codex

        started: list[tuple[str, str]] = []
        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
        )
        result = CodexAdapter(codex_factory=factory, sdk_module=sdk).run(
            prompt="do the bounded task",
            repository_path=Path("C:/repo"),
            model="gpt-test",
            effort="high",
            on_turn_started=lambda thread_id, turn_id: started.append((thread_id, turn_id)),
        )
        self.assertEqual(started, [("thread-123", "turn-started-123")])
        self.assertEqual(result.turn_id, "turn-123")
        self.assertEqual(holder["codex"].thread.turn_kwargs["effort"], "high")

    def test_missing_published_sdk_is_a_structured_error(self) -> None:
        with self.assertRaises(RunnerError) as context:
            CodexAdapter().run(prompt="x", repository_path=Path("C:/repo"), model="m", effort="low")
        self.assertEqual(context.exception.code, "sdk_unavailable")

    @unittest.skipUnless(False, "requires explicit authenticated live Codex SDK environment")
    def test_live_sdk_case(self) -> None:
        self.fail("live evidence is run explicitly outside the default test suite")


if __name__ == "__main__":
    unittest.main()
