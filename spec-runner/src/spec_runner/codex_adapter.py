from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable

from .errors import RunnerError

SDK_VERSION = "0.155.1"


@dataclass(frozen=True)
class CodexWorkerResult:
    thread_id: str
    turn_id: str
    status: str
    error: str | None
    final_response: str | None
    item_count: int
    started_at: int | None
    completed_at: int | None

    def public(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "error": self.error,
            "final_response": self.final_response,
            "item_count": self.item_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "sdk_version": SDK_VERSION,
        }


class CodexAdapter:
    """Small adapter around the published Python Codex SDK.

    The import is intentionally delayed so deterministic installations can run
    their contract tests without starting a Codex process. No transport or
    JSON-RPC protocol is implemented here.
    """

    def __init__(self, *, codex_factory: Callable[[Any], Any] | None = None, sdk_module: Any | None = None):
        self._codex_factory = codex_factory
        self._sdk_module = sdk_module

    def run(
        self,
        *,
        prompt: str,
        repository_path: Path,
        model: str,
        effort: str,
        thread_id: str | None = None,
        on_turn_started: Callable[[str, str], None] | None = None,
        control_state: Callable[[], str | None] | None = None,
        on_control_applied: Callable[[str], None] | None = None,
    ) -> CodexWorkerResult:
        if self._sdk_module is None:
            try:
                import openai_codex as sdk_module
            except ImportError as exc:
                raise RunnerError(
                    "sdk_unavailable",
                    f"openai-codex=={SDK_VERSION} is not installed; install the package in the isolated environment",
                ) from exc
        else:
            sdk_module = self._sdk_module
        Codex = sdk_module.Codex
        CodexConfig = sdk_module.CodexConfig
        Sandbox = sdk_module.Sandbox

        if not prompt.strip():
            raise RunnerError("invalid_prompt", "Codex prompt must not be empty")
        if not model.strip() or not effort.strip():
            raise RunnerError("invalid_route", "model and effort must be non-empty")
        factory = self._codex_factory or (lambda config: Codex(config))
        config = CodexConfig(cwd=str(repository_path), client_version=SDK_VERSION)
        try:
            with factory(config) as codex:
                if thread_id:
                    thread = codex.thread_resume(
                        thread_id, cwd=str(repository_path), model=model, sandbox=Sandbox.workspace_write
                    )
                else:
                    thread = codex.thread_start(model=model, cwd=str(repository_path), sandbox=Sandbox.workspace_write)
                result_thread_id = str(getattr(thread, "id", ""))
                if not result_thread_id or result_thread_id == "None":
                    raise RunnerError("sdk_identity_missing", "Codex SDK returned no formal thread identifier")
                # The published SDK exposes Thread.turn() as the boundary at
                # which the formal turn identity becomes durable. Persist it
                # before waiting for model work so status/recovery can inspect
                # an active worker instead of guessing from a pending row.
                if hasattr(thread, "turn"):
                    turn = thread.turn(
                        prompt,
                        cwd=str(repository_path),
                        model=model,
                        effort=effort,
                        sandbox=Sandbox.workspace_write,
                    )
                    result_turn_id = str(getattr(turn, "id", ""))
                    if not result_turn_id or result_turn_id == "None":
                        raise RunnerError("sdk_identity_missing", "Codex SDK returned no formal turn identifier")
                    if on_turn_started is not None:
                        on_turn_started(result_thread_id, result_turn_id)
                    result = self._run_turn_with_control(
                        turn,
                        control_state=control_state,
                        on_control_applied=on_control_applied,
                    )
                else:
                    # Keep the fake adapter contract useful for isolated unit
                    # tests and older test doubles; the published SDK path
                    # above is the production boundary.
                    result = thread.run(
                        prompt,
                        cwd=str(repository_path),
                        model=model,
                        effort=effort,
                        sandbox=Sandbox.workspace_write,
                    )
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "sdk_execution_failed",
                "Codex SDK failed while creating or running the worker",
                details={"exception_type": type(exc).__name__},
            ) from exc

        result_thread_id = str(getattr(thread, "id", result_thread_id if "result_thread_id" in locals() else ""))
        result_turn_id = str(getattr(result, "id", ""))
        if not result_thread_id or result_thread_id == "None" or not result_turn_id or result_turn_id == "None":
            raise RunnerError("sdk_identity_missing", "Codex SDK returned no formal thread or turn identifier")
        result_status = getattr(result, "status", "unknown")
        return CodexWorkerResult(
            thread_id=result_thread_id,
            turn_id=result_turn_id,
            status=str(getattr(result_status, "value", result_status)),
            error=str(getattr(result, "error", "")) if getattr(result, "error", None) else None,
            final_response=getattr(result, "final_response", None),
            item_count=len(getattr(result, "items", []) or []),
            started_at=getattr(result, "started_at", None),
            completed_at=getattr(result, "completed_at", None),
        )

    @staticmethod
    def _run_turn_with_control(
        turn: Any,
        *,
        control_state: Callable[[], str | None] | None,
        on_control_applied: Callable[[str], None] | None,
    ) -> Any:
        """Run a published TurnHandle while honoring durable stop requests.

        The SDK keeps ``run`` synchronous, while ``TurnHandle.interrupt`` is a
        separate request. A small watcher is therefore required to bridge the
        Runner's SQLite control plane to the SDK without implementing a second
        transport or a parent-model scheduling loop. The watcher is only
        active for the lifetime of this turn and never advances a stage.
        """
        if control_state is None:
            return turn.run()
        interrupt = getattr(turn, "interrupt", None)
        if not callable(interrupt):
            raise RunnerError(
                "sdk_control_unsupported",
                "the installed Codex SDK turn handle does not expose interrupt()",
            )

        stop = threading.Event()
        finished = threading.Event()
        interrupt_error: list[Exception] = []

        def watch() -> None:
            while not finished.is_set():
                try:
                    requested = control_state()
                except Exception as exc:  # pragma: no cover - transport-specific
                    interrupt_error.append(exc)
                    return
                if requested in {"pause_requested", "cancel_requested"}:
                    try:
                        interrupt()
                    except Exception as exc:  # pragma: no cover - SDK-specific
                        interrupt_error.append(exc)
                    else:
                        if on_control_applied is not None:
                            try:
                                on_control_applied(requested)
                            except Exception as exc:  # pragma: no cover - callback-specific
                                interrupt_error.append(exc)
                    return
                stop.wait(0.1)

        watcher = threading.Thread(target=watch, name="spec-runner-turn-control", daemon=True)
        watcher.start()
        try:
            result = turn.run()
        finally:
            finished.set()
            stop.set()
            watcher.join(timeout=1.0)
        if interrupt_error:
            raise RunnerError(
                "sdk_interrupt_failed",
                "the Runner could not reconcile a Codex turn control request",
                details={"exception_type": type(interrupt_error[0]).__name__},
            ) from interrupt_error[0]
        return result

    def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
        """Archive one SDK thread and prove it appears in every required page."""
        try:
            import openai_codex as sdk_module
        except ImportError as exc:
            raise RunnerError("sdk_unavailable", f"openai-codex=={SDK_VERSION} is not installed") from exc
        Codex = sdk_module.Codex
        CodexConfig = sdk_module.CodexConfig
        factory = self._codex_factory or (lambda config: Codex(config))
        pages = 0
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            with factory(CodexConfig(cwd=str(repository_path), client_version=SDK_VERSION)) as codex:
                codex.thread_archive(thread_id)
                while True:
                    response = codex.thread_list(archived=True, cursor=cursor, limit=100)
                    pages += 1
                    if any(str(getattr(item, "id", "")) == thread_id for item in (getattr(response, "data", []) or [])):
                        return {"thread_id": thread_id, "archived": True, "pages_read": pages}
                    next_cursor = getattr(response, "next_cursor", None)
                    if not next_cursor:
                        break
                    if next_cursor in seen_cursors:
                        raise RunnerError("archive_readback_failed", "Codex archive pagination repeated a cursor")
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "archive_failed",
                "Codex SDK archive or archive readback failed",
                details={"exception_type": type(exc).__name__},
            ) from exc
        raise RunnerError("archive_readback_failed", "archived thread was not found in the complete page walk")
