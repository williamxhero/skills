from __future__ import annotations

import types
import unittest
import threading
from pathlib import Path
import sys
from unittest.mock import patch

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

    def read(self, *, include_turns: bool = False) -> object:
        status = types.SimpleNamespace(root=types.SimpleNamespace(type=types.SimpleNamespace(value="idle"), active_flags=[]))
        item = types.SimpleNamespace(type="userMessage", id="item-read-1", content=[types.SimpleNamespace(text="original requirement")])
        turn = types.SimpleNamespace(id="turn-read-1", status=types.SimpleNamespace(value="completed"), started_at=1, completed_at=2, duration_ms=3, error=None, items_view="full", items=[item])
        source = types.SimpleNamespace(id=self.id, status=status, turns=[turn] if include_turns else [])
        return types.SimpleNamespace(thread=source)


class FakeTurn:
    id = "turn-started-123"

    def run(self) -> FakeResult:
        return FakeResult()


class BlockingTurn(FakeTurn):
    def __init__(self) -> None:
        self.interrupted = threading.Event()
        self.started = threading.Event()

    def run(self) -> FakeResult:
        self.started.set()
        self.interrupted.wait(2.0)
        result = FakeResult()
        result.status = types.SimpleNamespace(value="interrupted")
        return result

    def interrupt(self) -> object:
        self.interrupted.set()
        return types.SimpleNamespace()


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

    def thread_resume(self, thread_id: str, **kwargs: object) -> FakeThread:
        self.resume_thread_id = thread_id
        self.resume_kwargs = kwargs
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
            ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
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
        self.assertEqual(holder["codex"].start_kwargs["approval_mode"], "deny_all")
        self.assertEqual(holder["codex"].thread.run_kwargs["effort"], "high")
        self.assertEqual(result.approval_mode, "deny_all")

    def test_clean_thread_creation_does_not_resume_fork_or_start_a_turn(self) -> None:
        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            holder["codex"] = FakeCodex(config)
            return holder["codex"]

        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
            ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
        )
        result = CodexAdapter(codex_factory=factory, sdk_module=sdk).start_clean_thread(
            repository_path=Path("C:/repo"), model="gpt-test"
        )
        self.assertEqual(result["thread_id"], "thread-123")
        self.assertFalse(result["turn_started"])
        self.assertFalse(result["forked"])
        self.assertIsNone(result["source_thread_id"])
        self.assertFalse(hasattr(holder["codex"], "resume_thread_id"))

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
            ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
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

    def test_resume_uses_the_restricted_approval_policy(self) -> None:
        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            holder["codex"] = FakeCodex(config)
            return holder["codex"]

        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
            ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
        )
        CodexAdapter(codex_factory=factory, sdk_module=sdk).run(
            prompt="continue the bounded task",
            repository_path=Path("C:/repo"),
            model="gpt-test",
            effort="high",
            thread_id="thread-existing",
        )
        self.assertEqual(holder["codex"].resume_thread_id, "thread-existing")
        self.assertEqual(holder["codex"].resume_kwargs["approval_mode"], "deny_all")

    def test_explicit_thread_read_does_not_start_a_turn(self) -> None:
        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            holder["codex"] = FakeCodex(config)
            holder["codex"].thread.id = "thread-existing"
            return holder["codex"]

        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
        )
        inspected = CodexAdapter(codex_factory=factory, sdk_module=sdk).read_thread(
            thread_id="thread-existing", repository_path=Path("C:/repo")
        )
        self.assertFalse(inspected["started_turn"])
        self.assertEqual(inspected["read_via"], "thread_resume_then_thread_read_without_overrides")
        self.assertEqual(inspected["thread_resume_overrides"], {})
        self.assertEqual(inspected["thread_status"], "idle")
        self.assertEqual(inspected["turns"][0]["turn_id"], "turn-read-1")
        self.assertEqual(inspected["turns"][0]["items"][0]["type"], "userMessage")
        self.assertEqual(inspected["completeness"]["state"], "complete")
        self.assertFalse(inspected["evidence_limits"]["source_stop_confirmed"])
        self.assertFalse(inspected["evidence_limits"]["ownership_transferred"])
        self.assertEqual(holder["codex"].resume_kwargs, {})

    def test_thread_read_fails_closed_on_identity_mismatch(self) -> None:
        def factory(config: object) -> FakeCodex:
            return FakeCodex(config)

        sdk = types.SimpleNamespace(CodexConfig=lambda **kwargs: kwargs, Codex=object)
        with self.assertRaisesRegex(RunnerError, "different thread"):
            CodexAdapter(codex_factory=factory, sdk_module=sdk).read_thread(
                thread_id="requested-thread", repository_path=Path("C:/repo")
            )

    def test_thread_read_omits_reasoning_and_opaque_tool_payloads(self) -> None:
        class OpaqueThread(FakeThread):
            def read(self, *, include_turns: bool = False) -> object:
                status = types.SimpleNamespace(root=types.SimpleNamespace(type=types.SimpleNamespace(value="idle"), active_flags=[]))
                items = [
                    types.SimpleNamespace(type="reasoning", id="reason-1", content=["hidden"]),
                    types.SimpleNamespace(type="mcpToolCall", id="mcp-1", server="fixture", tool="read", arguments={"token": "secret"}, status="completed"),
                ]
                turn = types.SimpleNamespace(id="turn-opaque", status=types.SimpleNamespace(value="completed"), items_view="full", items=items)
                source = types.SimpleNamespace(id=self.id, status=status, turns=[turn] if include_turns else [])
                return types.SimpleNamespace(thread=source)

        holder: dict[str, FakeCodex] = {}

        def factory(config: object) -> FakeCodex:
            codex = FakeCodex(config)
            codex.thread = OpaqueThread()
            codex.thread.id = "thread-opaque"
            holder["codex"] = codex
            return codex

        sdk = types.SimpleNamespace(CodexConfig=lambda **kwargs: kwargs, Codex=object)
        inspected = CodexAdapter(codex_factory=factory, sdk_module=sdk).read_thread(
            thread_id="thread-opaque", repository_path=Path("C:/repo")
        )
        self.assertEqual(inspected["completeness"]["state"], "complete")
        self.assertEqual(inspected["completeness"]["business_material_state"], "complete")
        self.assertEqual(inspected["omitted_item_count"], 1)
        self.assertTrue(inspected["turns"][0]["items"][0]["omitted"])
        self.assertNotIn("arguments", inspected["turns"][0]["items"][1])

    def test_interrupt_thread_fails_closed_without_public_arbitrary_turn_handle(self) -> None:
        state = {"status": "inProgress"}
        interrupt_calls: list[tuple[str, str]] = []

        class SourceThread(FakeThread):
            def read(self, *, include_turns: bool = False) -> object:
                status = types.SimpleNamespace(root=types.SimpleNamespace(type=types.SimpleNamespace(value="active"), active_flags=[]))
                turn = types.SimpleNamespace(id="turn-active", status=types.SimpleNamespace(value=state["status"]), items_view="full", items=[])
                source = types.SimpleNamespace(id=self.id, status=status, turns=[turn] if include_turns else [])
                return types.SimpleNamespace(thread=source)

        class Client:
            def turn_interrupt(self, thread_id: str, turn_id: str) -> object:
                interrupt_calls.append((thread_id, turn_id))
                state["status"] = "interrupted"
                return types.SimpleNamespace(ok=True, secret="hidden")

        class InterruptCodex(FakeCodex):
            def __init__(self, config: object):
                super().__init__(config)
                self.thread = SourceThread()
                self.thread.id = "thread-active"
                self._client = Client()

        sdk = types.SimpleNamespace(CodexConfig=lambda **kwargs: kwargs, Codex=object)
        with self.assertRaisesRegex(RunnerError, "public API"):
            CodexAdapter(codex_factory=lambda config: InterruptCodex(config), sdk_module=sdk).interrupt_thread(
                thread_id="thread-active", repository_path=Path("C:/repo")
            )
        self.assertEqual(interrupt_calls, [])

    def test_missing_published_sdk_is_a_structured_error(self) -> None:
        with patch.dict(sys.modules, {"openai_codex": None}):
            with self.assertRaises(RunnerError) as context:
                CodexAdapter().run(prompt="x", repository_path=Path("C:/repo"), model="m", effort="low")
        self.assertEqual(context.exception.code, "sdk_unavailable")

    def test_control_request_interrupts_active_published_turn(self) -> None:
        holder: dict[str, FakeCodex] = {}
        turn = BlockingTurn()

        def factory(config: object) -> FakeCodex:
            codex = FakeCodex(config)
            codex.thread = FakeThreadWithTurn()
            codex.thread.turn = lambda prompt, **kwargs: turn  # type: ignore[method-assign]
            holder["codex"] = codex
            return codex

        requests = iter([None, "pause_requested"])
        applied: list[str] = []
        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
            ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
        )
        result = CodexAdapter(codex_factory=factory, sdk_module=sdk).run(
            prompt="do the bounded task",
            repository_path=Path("C:/repo"),
            model="gpt-test",
            effort="high",
            control_state=lambda: next(requests, "pause_requested"),
            on_control_applied=applied.append,
        )
        self.assertTrue(turn.started.is_set())
        self.assertTrue(turn.interrupted.is_set())
        self.assertEqual(applied, ["pause_requested"])
        self.assertEqual(result.status, "interrupted")

    def test_missing_approval_policy_is_a_structured_capability_error(self) -> None:
        sdk = types.SimpleNamespace(
            CodexConfig=lambda **kwargs: kwargs,
            Codex=object,
            Sandbox=types.SimpleNamespace(workspace_write="workspace-write"),
        )
        with self.assertRaises(RunnerError) as context:
            CodexAdapter(codex_factory=lambda config: FakeCodex(config), sdk_module=sdk).run(
                prompt="do the bounded task",
                repository_path=Path("C:/repo"),
                model="gpt-test",
                effort="high",
            )
        self.assertEqual(context.exception.code, "sdk_approval_policy_unsupported")

    def test_control_watch_requires_interrupt_capability(self) -> None:
        with self.assertRaises(RunnerError) as context:
            CodexAdapter._run_turn_with_control(
                FakeTurn(), control_state=lambda: "pause_requested", on_control_applied=None
            )
        self.assertEqual(context.exception.code, "sdk_control_unsupported")

    @unittest.skipUnless(False, "requires explicit authenticated live Codex SDK environment")
    def test_live_sdk_case(self) -> None:
        self.fail("live evidence is run explicitly outside the default test suite")


if __name__ == "__main__":
    unittest.main()
