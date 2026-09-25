from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, replace
from pathlib import Path
import re
import threading
from typing import Any, Callable

from .errors import RunnerError
from .matt import render_prompt, resolve_local_skill
from .recovery import observation_from_error, observation_from_worker_result

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
    fault_observation: dict[str, object] | None = None

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
        if self.fault_observation is not None:
            result["fault_observation"] = self.fault_observation
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
            details = getattr(exc, "details", {}) or {}
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
                    "exception_message": str(exc)[:500],
                    "model": model,
                    "thread_id": result_thread_id if "result_thread_id" in locals() else None,
                    "turn_id": result_turn_id if "result_turn_id" in locals() else None,
                    "external_result_requires_reconciliation": "result_turn_id" in locals(),
                    "fault_observation": observation_from_error(
                        operation_kind="codex_turn",
                        error=exc,
                        thread_id=result_thread_id if "result_thread_id" in locals() else None,
                        turn_id=result_turn_id if "result_turn_id" in locals() else None,
                        requested_model=model,
                        requested_effort=effort,
                        sdk_version=SDK_VERSION,
                    ).public(),
                },
            ) from exc

        result_thread_id = str(getattr(thread, "id", result_thread_id if "result_thread_id" in locals() else ""))
        result_turn_id = str(getattr(result, "id", ""))
        if not result_thread_id or result_thread_id == "None" or not result_turn_id or result_turn_id == "None":
            raise RunnerError("sdk_identity_missing", "Codex SDK returned no formal thread or turn identifier")
        result_status = getattr(result, "status", "unknown")
        result_error = getattr(result, "error", None)
        fault_observation = None
        if result_error:
            fault_observation = observation_from_error(
                operation_kind="codex_turn",
                error={
                    "message": result_error,
                    "source": "sdk_result",
                    "structured": False,
                    "thread_id": result_thread_id,
                    "turn_id": result_turn_id,
                    "request_admission": "accepted",
                    "execution_outcome": "failed",
                },
                thread_id=result_thread_id,
                turn_id=result_turn_id,
                requested_model=model,
                requested_effort=effort,
                sdk_version=SDK_VERSION,
            ).public()
        elif str(getattr(result_status, "value", result_status)) == "failed":
            observed = observation_from_worker_result(
                operation_kind="codex_turn",
                result={
                    "status": "failed",
                    "thread_id": result_thread_id,
                    "turn_id": result_turn_id,
                },
                requested_model=model,
                requested_effort=effort,
            )
            fault_observation = observed.public() if observed is not None else None
        return CodexWorkerResult(
            thread_id=result_thread_id,
            turn_id=result_turn_id,
            status=str(getattr(result_status, "value", result_status)),
            error=str(result_error) if result_error else None,
            final_response=getattr(result, "final_response", None),
            item_count=len(getattr(result, "items", []) or []),
            started_at=getattr(result, "started_at", None),
            completed_at=getattr(result, "completed_at", None),
            approval_mode=APPROVAL_MODE,
            fault_observation=fault_observation,
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

    def unarchive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
        """Make an archived implementation thread resumable and prove identity."""
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
        try:
            with factory(CodexConfig(cwd=str(repository_path), client_version=SDK_VERSION)) as codex:
                try:
                    codex.thread_unarchive(thread_id)
                except Exception as exc:
                    # The operation is intentionally idempotent: a prior
                    # recovery process may already have restored the thread.
                    if "no archived rollout" not in str(exc).lower():
                        raise
                thread = codex.thread_resume(thread_id, cwd=str(repository_path))
                resumed_id = str(getattr(thread, "id", ""))
                if resumed_id != thread_id:
                    raise RunnerError("unarchive_readback_failed", "unarchived thread identity did not match")
                return {"thread_id": thread_id, "archived": False, "resumed": True}
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "unarchive_failed",
                "Codex SDK unarchive or resume readback failed",
                details={"exception_type": type(exc).__name__},
            ) from exc

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
        factory = self._codex_factory or (lambda config: Codex(config))
        try:
            # ``thread_resume`` is currently the SDK's handle acquisition
            # boundary.  It accepts optional thread configuration overrides,
            # but inspection must not change the source thread's cwd,
            # permissions, model, or approval policy.  Keep the client
            # process configuration independent of the source thread too.
            with factory(CodexConfig(client_version=SDK_VERSION)) as codex:
                thread = codex.thread_resume(thread_id)
                response = thread.read(include_turns=True)
                source = getattr(response, "thread", None)
                if source is None:
                    raise RunnerError("sdk_thread_read_invalid", "Codex SDK returned no thread in read response")
                observed_thread_id = str(getattr(source, "id", thread_id))
                if observed_thread_id != thread_id:
                    raise RunnerError(
                        "sdk_thread_identity_mismatch",
                        "Codex SDK returned a different thread than the requested source",
                        details={"requested_thread_id": thread_id, "observed_thread_id": observed_thread_id},
                    )
                status = getattr(source, "status", None)
                status_root = getattr(status, "root", status)
                thread_status = _enum_value(getattr(status_root, "type", "unknown"))
                active_flags = _jsonable(getattr(status_root, "active_flags", []), _key="active_flags")
                turns = getattr(source, "turns", []) or []
                projected_turns: list[dict[str, object]] = []
                business_items: list[dict[str, object]] = []
                omitted_item_count = 0
                completeness = "complete"
                completeness_reasons: list[str] = []
                for turn in turns:
                    items = getattr(turn, "items", None)
                    items_view = _enum_value(getattr(turn, "items_view", None))
                    if not hasattr(turn, "items"):
                        completeness = "unknown"
                        completeness_reasons.append("turn_items_field_missing")
                    elif items_view not in {None, "full"}:
                        completeness = "partial"
                        completeness_reasons.append(f"turn_items_{items_view}")
                    projected_turn: dict[str, object] = {
                        "turn_id": str(getattr(turn, "id", "")),
                        "status": _enum_value(getattr(turn, "status", "unknown")),
                        "started_at": getattr(turn, "started_at", None),
                        "completed_at": getattr(turn, "completed_at", None),
                        "duration_ms": getattr(turn, "duration_ms", None),
                        "error": _jsonable(getattr(turn, "error", None)),
                        "items_view": items_view or "unknown",
                        "items": [],
                    }
                    for item in items or []:
                        projected = _project_item(item)
                        if projected.get("omitted"):
                            omitted_item_count += 1
                        else:
                            business_items.append({"turn_id": projected_turn["turn_id"], "item": projected})
                        projected_turn["items"].append(projected)
                    projected_turns.append(projected_turn)
                if not hasattr(source, "turns"):
                    completeness = "unknown"
                    completeness_reasons.append("thread_turns_field_missing")
                # Omitted reasoning, encrypted, and opaque tool payloads are
                # deliberate redactions. They do not make the visible business
                # history incomplete; only an SDK omission or pagination does.
                if omitted_item_count:
                    completeness_reasons.append("non_business_items_omitted")
                history_mode = _enum_value(getattr(source, "history_mode", None))
                if history_mode in {"paginated", "compressed"}:
                    completeness = "partial"
                    completeness_reasons.append(f"history_mode_{history_mode}")
                return {
                    "schema_version": "spec-runner-sdk-thread-inspection/v1",
                    "thread_id": observed_thread_id,
                    "repository_path": str(repository_path),
                    "read_via": "thread_resume_then_thread_read_without_overrides",
                    "started_turn": False,
                    "thread_resume_overrides": {},
                    "codex_config_overrides": {"client_version": SDK_VERSION},
                    "thread_status": thread_status,
                    "active_flags": active_flags,
                    "thread": _thread_metadata(source),
                    "turns": projected_turns,
                    "business_items": business_items,
                    "omitted_item_count": omitted_item_count,
                    "turn_count": len(turns),
                    "completeness": {
                        "state": completeness,
                        "business_material_state": completeness,
                        "include_turns_requested": True,
                        "reasons": sorted(set(completeness_reasons)),
                    },
                    "evidence_limits": {
                        "source_stop_confirmed": False,
                        "ownership_transferred": False,
                        "read_only_observation": True,
                    },
                }
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "sdk_thread_read_failed",
                "Codex SDK failed while reading the explicitly supplied thread",
                details={"exception_type": type(exc).__name__},
            ) from exc

    def interrupt_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
        """Interrupt an actually running source turn, then read it back.

        SDK 0.155.1 does not expose a public method that creates a
        ``TurnHandle`` for an arbitrary existing turn.  After identifying an
        in-progress turn through a read, this method reports that capability
        boundary without using private SDK members. A thread/turn interrupt is
        not treated as proof that an external scheduler has stopped assigning
        work.
        """
        before = self.read_thread(thread_id=thread_id, repository_path=repository_path)
        running = [
            turn for turn in before.get("turns", [])
            if isinstance(turn, dict) and turn.get("status") in {"inProgress", "running"}
        ]
        if not running:
            return {
                "schema_version": "spec-runner-sdk-thread-interrupt/v1",
                "thread_id": thread_id,
                "accepted": False,
                "reason": "no_active_turn",
                "observation_before": before,
                "evidence_limits": {"dispatcher_quiesced": False, "ownership_transferred": False},
            }
        turn_id = running[-1].get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RunnerError("sdk_interrupt_identity_missing", "active source turn has no stable turn identifier")
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
        try:
            with factory(CodexConfig(client_version=SDK_VERSION)) as codex:
                # The supported SDK only exposes interrupt on a TurnHandle
                # returned by the same process that started the turn. It has
                # no public way to obtain such a handle for an arbitrary
                # historical turn ID, so never reach into the private client.
                raise RunnerError(
                    "sdk_interrupt_unsupported",
                    "installed Codex SDK cannot interrupt an arbitrary existing turn through its public API",
                    details={"thread_id": thread_id, "turn_id": turn_id},
                )
        except RunnerError:
            raise
        except Exception as exc:
            raise RunnerError(
                "sdk_interrupt_failed",
                "Codex SDK failed while interrupting the explicitly supplied source turn",
                details={"exception_type": type(exc).__name__, "thread_id": thread_id, "turn_id": turn_id},
            ) from exc
        after = self.read_thread(thread_id=thread_id, repository_path=repository_path)
        remaining = [
            turn for turn in after.get("turns", [])
            if isinstance(turn, dict) and turn.get("turn_id") == turn_id and turn.get("status") in {"inProgress", "running"}
        ]
        if remaining:
            raise RunnerError(
                "sdk_interrupt_readback_failed",
                "source turn interrupt was accepted but the turn is still active on readback",
                details={"thread_id": thread_id, "turn_id": turn_id},
            )
        return {
            "schema_version": "spec-runner-sdk-thread-interrupt/v1",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "accepted": True,
            "interrupt_response": _jsonable(response),
            "observation_before": before,
            "observation_after": after,
            "evidence_limits": {"dispatcher_quiesced": False, "ownership_transferred": False},
        }


def _enum_value(value: Any) -> object:
    """Return JSON-safe enum values while keeping fake SDKs usable."""
    if isinstance(value, Enum):
        return value.value
    value_attr = getattr(value, "value", None)
    return value_attr if value_attr is not None else value


def _jsonable(value: Any, *, _key: str | None = None) -> object:
    """Project SDK models into bounded JSON without leaking credential fields."""
    if _key and any(token in _key.lower() for token in ("token", "secret", "password", "api_key", "authorization")):
        return "[redacted]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item, _key=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json", by_alias=True))
        except TypeError:
            return _jsonable(model_dump())
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {str(key): _jsonable(item, _key=str(key)) for key, item in attributes.items() if not str(key).startswith("_")}
    return str(value)


_SECRET_TEXT = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*([^\s,;]+)"
)


def _safe_text(value: Any, *, limit: int = 12000) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _SECRET_TEXT.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _project_item(item: Any) -> dict[str, object]:
    """Keep visible business material and omit hidden or opaque rollout data."""
    source = getattr(item, "root", item)
    kind = _enum_value(getattr(source, "type", None))
    item_id = str(getattr(source, "id", ""))
    common: dict[str, object] = {"type": kind or "unknown", "id": item_id}
    if kind == "userMessage":
        common["content"] = _jsonable(getattr(source, "content", []))
    elif kind == "agentMessage":
        common.update({
            "text": _safe_text(getattr(source, "text", "")),
            "phase": _enum_value(getattr(source, "phase", None)),
            "questions": _jsonable(getattr(source, "questions", None)),
        })
    elif kind == "plan":
        common["text"] = _safe_text(getattr(source, "text", ""))
    elif kind == "commandExecution":
        common.update({
            "command": _safe_text(getattr(source, "command", "")),
            "cwd": _safe_text(getattr(source, "cwd", "")),
            "status": _enum_value(getattr(source, "status", None)),
            "exit_code": getattr(source, "exit_code", None),
            "duration_ms": getattr(source, "duration_ms", None),
            "aggregated_output": _safe_text(getattr(source, "aggregated_output", None)),
        })
    elif kind == "fileChange":
        changes = []
        for change in getattr(source, "changes", []) or []:
            change_source = getattr(change, "root", change)
            changes.append({
                "path": _safe_text(getattr(change_source, "path", None) or getattr(change_source, "target", None)),
                "kind": _enum_value(getattr(change_source, "kind", None) or getattr(change_source, "type", None)),
            })
        common.update({"status": _enum_value(getattr(source, "status", None)), "changes": changes})
    elif kind == "mcpToolCall":
        common.update({
            "server": _safe_text(getattr(source, "server", None)),
            "tool": _safe_text(getattr(source, "tool", None)),
            "status": _enum_value(getattr(source, "status", None)),
            "read_only_hint": getattr(source, "read_only_hint", None),
            "duration_ms": getattr(source, "duration_ms", None),
        })
    elif kind == "webSearch":
        common["query"] = _safe_text(getattr(source, "query", None))
    else:
        return {"type": kind or "unknown", "id": item_id, "omitted": True, "reason": "non_business_item"}
    return common


def _thread_metadata(source: Any) -> dict[str, object]:
    fields = (
        "id", "name", "preview", "cwd", "created_at", "updated_at", "recency_at",
        "model", "model_provider", "reasoning_effort", "source", "originator",
        "session_id", "project_id", "forked_from_id", "parent_thread_id", "history_mode",
        "git_info", "path", "ephemeral", "history_mode",
    )
    metadata = {field: _jsonable(getattr(source, field), _key=field) for field in fields if hasattr(source, field)}
    status = getattr(source, "status", None)
    if status is not None:
        status_root = getattr(status, "root", status)
        metadata["status"] = _jsonable(getattr(status_root, "type", status_root), _key="status")
        metadata["active_flags"] = _jsonable(getattr(status_root, "active_flags", []), _key="active_flags")
    return metadata
