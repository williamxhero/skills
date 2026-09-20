"""Run a live app-server probe using cwd plus saved-project sidecar identity."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from app_server_bridge import AppServerBridge  # noqa: E402
from task_backend import BackendError  # noqa: E402


def _turn_status(readback: dict[str, Any]) -> str | None:
    thread = readback.get("thread", readback)
    turns = thread.get("turns") if isinstance(thread, dict) else None
    if not isinstance(turns, list) or not turns:
        return None
    last = turns[-1]
    if not isinstance(last, dict):
        return None
    status = last.get("status")
    if isinstance(status, dict):
        status = status.get("type")
    return status if isinstance(status, str) else None


def _history_turns(readback: dict[str, Any]) -> list[dict[str, Any]]:
    thread = readback.get("thread", readback)
    turns = thread.get("turns") if isinstance(thread, dict) else None
    if not isinstance(turns, list):
        return []
    return [turn for turn in turns if isinstance(turn, dict)]


def _turn_output(turn: dict[str, Any]) -> str:
    """Collect assistant text from the app-server turn item shape."""
    texts: list[str] = []
    direct = turn.get("text") or turn.get("output") or turn.get("result")
    if isinstance(direct, str):
        texts.append(direct)
    items = turn.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return "\n".join(texts)


def _read_persisted_history(bridge: AppServerBridge, formal_thread_id: str, turn_id: str,
                            *, timeout: float, retry_interval: float = 0.5,
                            sleep_fn=time.sleep) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read completed-turn history, allowing only the known persistence race.

    ``turn/completed`` is the execution barrier.  The app-server may still be
    materializing the rollout when the first ``thread/read`` arrives, so an
    empty history or the known ``rollout is empty`` error is retried for a
    bounded interval.  Every other error fails immediately.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    attempts: list[dict[str, Any]] = []
    while True:
        try:
            readback = bridge.read_history(formal_thread_id, turn_id)
            turns = _history_turns(readback)
            matching = [turn for turn in turns if turn.get("id") == turn_id]
            attempts.append({"kind": "read", "turn_count": len(turns),
                             "matching_turn": bool(matching)})
            if matching:
                return readback, attempts
        except BackendError as exc:
            message = str(exc)
            attempts.append({"kind": "error", "message": message})
            if "rollout" not in message.lower() or "empty" not in message.lower():
                raise
        if time.monotonic() >= deadline:
            raise BackendError(
                f"turn history did not persist for {formal_thread_id}/{turn_id}"
            )
        sleep_fn(min(retry_interval, max(0.0, deadline - time.monotonic())))


def _archive_probe_thread(bridge: AppServerBridge, formal_thread_id: str,
                          host_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Close a live-probe thread and prove the close with an independent readback."""
    common = {"formal_thread_id": formal_thread_id, "host_id": host_id}
    archive = bridge.request("set_thread_archived", common)
    readback = bridge.request("read_archive_state", common)
    if readback.get("archived") is not True:
        raise BackendError(f"live probe archive readback is incomplete for {formal_thread_id}")
    return archive, readback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-evidence", action="append", required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    cwd = str(Path.cwd())
    run_id = f"qualification-v3-cwd-{int(time.time())}"
    identity = {
        "run_id": run_id,
        "task_id": "v3-cwd-probe-live",
        "attempt_id": "01",
        "owner_id": "qualification-controller",
        "cwd": cwd,
        "project_id": args.project_id,
        "project_id_source": "saved_project_readback",
        "project_canonical_path": cwd,
        "project_identity_evidence": args.project_evidence,
        "model": args.model,
        "effort": args.effort,
        "host_id": "local",
    }
    bridge = AppServerBridge(["codex", "app-server", "--stdio"], args.metadata, host_id="local")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "backend": "app-server-bridge",
        "project_identity": {
            "project_id": args.project_id,
            "project_id_source": "saved_project_readback",
            "canonical_path": cwd,
            "evidence": args.project_evidence,
        },
    }
    formal: str | None = None
    failure: BaseException | None = None
    try:
        created = bridge.request("create_thread", identity)
        evidence["create"] = created
        formal = created["formal_thread_id"]
        common = {"formal_thread_id": formal, "host_id": "local"}
        evidence["identity_readback"] = bridge.request("read_thread", common)
        evidence["route_readback"] = bridge.request("read_applied_route", common)
        evidence["list_readback"] = bridge.request("list_tasks", common)
        evidence["dry_run"] = {
            "create": bridge.request("create_thread", {"dry_run": True}),
            "send": bridge.request("send_message_to_thread", {
                **common, "message": "probe", "dry_run": True,
            }),
            "archive": bridge.request("set_thread_archived", {**common, "dry_run": True}),
        }
        evidence["turn_start"] = bridge.send_message_to_thread(
            **common,
            message="Reply exactly V3_PROBE_READY. Do not modify files, Git, GitHub, tasks, or external state.",
        )
        turn_id = evidence["turn_start"].get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise BackendError("probe turn start did not return turn_id")
        completion = bridge.wait_for_turn_completion(formal, turn_id, timeout=args.timeout)
        evidence["turn_completed"] = completion
        history, history_attempts = _read_persisted_history(
            bridge, formal, turn_id, timeout=args.timeout,
        )
        evidence["history_read_attempts"] = history_attempts
        evidence["final_thread_readback"] = history
        evidence["final_identity_readback"] = bridge.request("read_thread", common)
        output = ""
        for turn in _history_turns(history):
            if turn.get("id") != turn_id:
                continue
            output = _turn_output(turn)
        if "V3_PROBE_READY" not in output:
            raise BackendError("probe persisted output does not contain V3_PROBE_READY")
    except BaseException as exc:
        failure = exc
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if formal is not None:
            try:
                archive, archive_readback = _archive_probe_thread(bridge, formal, "local")
                evidence["archive"] = archive
                evidence["archive_readback"] = archive_readback
            except Exception as cleanup_error:
                evidence["archive_error"] = {
                    "type": type(cleanup_error).__name__, "message": str(cleanup_error),
                }
                if failure is None:
                    failure = BackendError(
                        f"live probe cleanup failed for {formal}: {cleanup_error}"
                    )
                    evidence["error"] = {"type": type(failure).__name__, "message": str(failure)}
        evidence["decision"] = "allow" if failure is None else "reject"
        try:
            bridge.close()
        finally:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failure is not None:
        raise failure
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
