from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import Any, Callable

from .errors import RunnerError
from .matt import render_prompt, resolve_local_skill

SDK_VERSION = "0.155.1"
APPROVAL_MODE = "deny_all"


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
    approval_mode: str = APPROVAL_MODE
    skill_observation: dict[str, object] | None = None

    def public(self) -> dict[str, object]:
        result = {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "error": self.error,
            "final_response": self.final_response,
            "item_count": self.item_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "sdk_version": SDK_VERSION,
            "approval_mode": self.approval_mode,
        }
        if self.skill_observation is not None:
            result["skill_observation"] = self.skill_observation
        return result


class CodexAdapter:
    """Small adapter around the published Python Codex SDK.

    The import is intentionally delayed so deterministic installations can run
    their contract tests without starting a Codex process. No transport or
    JSON-RPC protocol is implemented here.
    """

    def __init__(self, *, codex_factory: Callable[[Any], Any] | None = None, sdk_module: Any | None = None):
        self._codex_factory = codex_factory
        self._sdk_module = sdk_module

    def run_semantic(
        self,
        *,
        phase: str,
        repository_path: Path,
        model: str,
        effort: str,
        trusted: dict[str, Any],
        untrusted: dict[str, Any],
        schema: dict[str, Any],
        skill_roots: tuple[Path, ...] = (),
        skill_config: Path | None = None,
        thread_id: str | None = None,
        on_turn_started: Callable[[str, str], None] | None = None,
        control_state: Callable[[], str | None] | None = None,
        on_control_applied: Callable[[str], None] | None = None,
    ) -> CodexWorkerResult:
        """Resolve the current Skill at every SDK turn, including resume.

        SDK 0.155.1 accepts SkillInput and a formal output_schema.  The
        current body is also in this turn's text so a resumed thread cannot
        silently rely on an older cached Skill rendering.
        """
        skill = resolve_local_skill(phase, roots=skill_roots, config_file=skill_config)
        rendered = render_prompt(phase=phase, lock=skill, trusted=trusted, untrusted=untrusted, schema=schema)
        result = self.run(
            prompt=str(rendered["prompt"]),
            repository_path=repository_path,
            model=model,
            effort=effort,
            thread_id=thread_id,
            read_only=phase != "implement",
            skill_ref=(skill.name, str(skill.path)),
            output_schema=schema or None,
            on_turn_started=on_turn_started,
            control_state=control_state,
            on_control_applied=on_control_applied,
        )
        return replace(result, skill_observation={
            "phase": phase,
            "source": str(skill.path),
            "source_digest": skill.sha256,
            "read_at_ns": skill.read_at_ns,
            "prompt_digest": rendered["prompt_digest"],
            "injection": "sdk_skill_input_plus_current_body",
        })

    def run(
        self,
        *,
        prompt: str,
        repository_path: Path,
        model: str,
        effort: str,
        thread_id: str | None = None,
        read_only: bool = False,
        skill_ref: tuple[str, str] | None = None,
        output_schema: dict[str, Any] | None = None,
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
        sandbox = Sandbox.read_only if read_only else Sandbox.workspace_write
        sdk_input: Any = prompt
        if skill_ref is not None:
            skill_input = getattr(sdk_module, "SkillInput", None)
            text_input = getattr(sdk_module, "TextInput", None)
            if callable(skill_input) and callable(text_input):
                sdk_input = [text_input(prompt), skill_input(name=skill_ref[0], path=skill_ref[1])]
            elif self._sdk_module is None:
                raise RunnerError("sdk_skill_input_unsupported", "installed SDK does not expose SkillInput and TextInput")
            # Isolated fake SDKs may test the equivalent full-text fallback.
        approval_modes = getattr(sdk_module, "ApprovalMode", None)
        approval_mode = getattr(approval_modes, APPROVAL_MODE, None)
        if approval_mode is None:
            raise RunnerError(
                "sdk_approval_policy_unsupported",
                f"openai-codex=={SDK_VERSION} does not expose the required {APPROVAL_MODE} approval policy",
            )

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
                        thread_id,
                        cwd=str(repository_path),
                        model=model,
                        sandbox=sandbox,
                        approval_mode=approval_mode,
                    )
                else:
                    thread = codex.thread_start(
                        model=model,
                        cwd=str(repository_path),
                        sandbox=sandbox,
                        approval_mode=approval_mode,
                    )
                result_thread_id = str(getattr(thread, "id", ""))
                if not result_thread_id or result_thread_id == "None":
                    raise RunnerError("sdk_identity_missing", "Codex SDK returned no formal thread identifier")
                # The published SDK exposes Thread.turn() as the boundary at
                # which the formal turn identity becomes durable. Persist it
                # before waiting for model work so status/recovery can inspect
                # an active worker instead of guessing from a pending row.
                if hasattr(thread, "turn"):
                    turn = thread.turn(
                        sdk_input,
                        cwd=str(repository_path),
                        model=model,
                        effort=effort,
                        sandbox=sandbox,
                        **({"output_schema": output_schema} if output_schema is not None else {}),
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
                        sdk_input,
                        cwd=str(repository_path),
                        model=model,
                        effort=effort,
                        sandbox=sandbox,
                        **({"output_schema": output_schema} if output_schema is not None else {}),
                    )
        except RunnerError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "does not exist or you do not have access" in message:
                code = "sdk_model_unavailable"
                description = "the requested model was rejected by the current Codex account or runtime"
            elif "rate limit" in message or "429" in message:
                code = "sdk_rate_limited"
                description = "Codex SDK turn was rate limited"
            elif "unauthorized" in message or "401" in message:
                code = "sdk_authentication_failed"
                description = "Codex SDK authentication failed"
            elif "timed out" in message or "timeout" in message:
                code = "sdk_timeout"
                description = "Codex SDK turn timed out"
            elif "invalid_json_schema" in message or "invalid schema" in message:
                code = "sdk_schema_invalid"
                description = "the SDK rejected the formal output schema"
            else:
                code = "sdk_execution_failed"
                description = "Codex SDK failed while creating or running the worker"
            raise RunnerError(
                code,
                description,
                details={
                    "exception_type": type(exc).__name__,
                    "model": model,
                    "thread_id": result_thread_id if "result_thread_id" in locals() else None,
                    "turn_id": result_turn_id if "result_turn_id" in locals() else None,
                    "external_result_requires_reconciliation": "result_turn_id" in locals(),
                },
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
            approval_mode=APPROVAL_MODE,
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
        if self._sdk_module is None:
            try:
                import openai_codex as sdk_module
            except ImportError as exc:
                raise RunnerError("sdk_unavailable", f"openai-codex=={SDK_VERSION} is not installed") from exc
        else:
            sdk_module = self._sdk_module
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

    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
        """Read one explicitly supplied SDK thread without starting a turn.

        The published SDK exposes thread history through a ``Thread`` handle;
        obtaining that handle requires ``thread_resume``. This method records
        that boundary explicitly and only calls ``Thread.read`` afterwards. It
        never calls ``turn``, ``run``, ``steer``, ``interrupt``, or archive.
        It is therefore suitable for an explicit takeover inspection, not an
        ownership transfer of an active external writer.
        """
        if not thread_id.strip():
            raise RunnerError("invalid_thread_id", "thread_id must be non-empty")
        if self._sdk_module is None:
            try:
                import openai_codex as sdk_module
            except ImportError as exc:
                raise RunnerError("sdk_unavailable", f"openai-codex=={SDK_VERSION} is not installed") from exc
        else:
            sdk_module = self._sdk_module
        Codex = sdk_module.Codex
        CodexConfig = sdk_module.CodexConfig
        Sandbox = sdk_module.Sandbox
        approval_modes = getattr(sdk_module, "ApprovalMode", None)
        approval_mode = getattr(approval_modes, APPROVAL_MODE, None)
        if approval_mode is None:
            raise RunnerError(
                "sdk_approval_policy_unsupported",
                f"openai-codex=={SDK_VERSION} does not expose the required {APPROVAL_MODE} approval policy",
            )
        factory = self._codex_factory or (lambda config: Codex(config))
        try:
            with factory(CodexConfig(cwd=str(repository_path), client_version=SDK_VERSION)) as codex:
                thread = codex.thread_resume(
                    thread_id,
                    cwd=str(repository_path),
                    sandbox=Sandbox.workspace_write,
                    approval_mode=approval_mode,
                )
                response = thread.read(include_turns=True)
                source = getattr(response, "thread", None)
                if source is None:
                    raise RunnerError("sdk_thread_read_invalid", "Codex SDK returned no thread in read response")
                status = getattr(source, "status", None)
                status_root = getattr(status, "root", status)
                flags = getattr(status_root, "active_flags", []) or []
                turns = getattr(source, "turns", []) or []
                return {
                    "schema_version": "spec-runner-sdk-thread-inspection/v1",
                    "thread_id": str(getattr(source, "id", thread_id)),
                    "repository_path": str(repository_path),
                    "read_via": "thread_resume_then_thread_read",
                    "started_turn": False,
                    "approval_mode": APPROVAL_MODE,
                    "thread_status": str(getattr(getattr(status_root, "type", None), "value", getattr(status_root, "type", "unknown"))),
                    "active_flags": [str(getattr(flag, "value", flag)) for flag in flags],
                    "turns": [
                        {
                            "turn_id": str(getattr(turn, "id", "")),
                            "status": str(getattr(getattr(turn, "status", None), "value", getattr(turn, "status", "unknown"))),
                            "started_at": getattr(turn, "started_at", None),
                            "completed_at": getattr(turn, "completed_at", None),
                        }
                        for turn in turns
                    ],
                    "turn_count": len(turns),
                }
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "sdk_thread_read_failed",
                "Codex SDK failed while reading the explicitly supplied thread",
                details={"exception_type": type(exc).__name__},
            ) from exc
