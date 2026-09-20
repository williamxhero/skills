"""Normalize Codex app-server JSON-RPC into the task-backend contract.

The app-server owns native thread identity, lifecycle, cwd, project, model, and
reasoning effort.  It does not expose the controller's task/run/attempt/owner
fields, so this bridge reads those fields from a controller-owned metadata file.
Metadata is evidence, never a guess: a missing record or a disagreement with a
native thread readback fails the operation.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from task_backend import BackendError, JsonRpcStdioTransport

REQUIRED_IDENTITY = (
    "formal_thread_id", "host_id", "task_id", "run_id", "attempt_id",
    "owner_id", "cwd", "project_id",
)
REQUIRED_ENROLLMENT_EVIDENCE = "identity_evidence"


class AppServerBridge:
    """One live app-server session plus one controller metadata sidecar."""

    def __init__(self, command: list[str], metadata_path: Path, *, host_id: str = "local",
                 transport: Any | None = None):
        self.transport = transport or JsonRpcStdioTransport(command)
        self.metadata_path = metadata_path
        self.host_id = host_id
        self.initialized = False

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        if operation == "capabilities":
            return self.capabilities()
        self._initialize()
        if operation == "list_tasks":
            return self.list_tasks(**params)
        if operation == "read_thread":
            return self.read_thread(**params)
        if operation == "read_applied_route":
            return self.read_applied_route(**params)
        if operation == "read_archive_state":
            return self.read_archive_state(**params)
        if operation == "create_thread":
            return self.create_thread(**params)
        if operation == "send_message_to_thread":
            return self.send_message_to_thread(**params)
        if operation == "wait_for_turn_completion":
            return self.wait_for_turn_completion(**params)
        if operation == "read_history":
            return self.read_history(**params)
        if operation == "set_thread_archived":
            return self.set_thread_archived(**params)
        if operation == "adopt_thread":
            return self.adopt_thread(**params)
        raise BackendError(f"unsupported app-server bridge operation: {operation}")

    def _initialize(self) -> None:
        if self.initialized:
            return
        self._result(
            "initialize",
            {
                "clientInfo": {"name": "implement-needs", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.initialized = True

    def _result(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        response = self.transport.request(method, params)
        if not isinstance(response, Mapping) or response.get("error") is not None:
            raise BackendError(f"app-server {method} failed: {response}")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise BackendError(f"app-server {method} returned no object result")
        return dict(result)

    def _metadata(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackendError("app-server identity metadata is unreadable") from exc
        entries = raw.get("threads") if isinstance(raw, Mapping) else None
        if not isinstance(entries, list):
            raise BackendError("app-server identity metadata has no threads list")
        indexed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            validated = self._validate_metadata_entry(entry)
            formal = validated["formal_thread_id"]
            if formal in indexed:
                raise BackendError("app-server identity metadata has duplicate or missing formal_thread_id")
            indexed[formal] = validated
        return indexed

    @staticmethod
    def _validate_metadata_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, Mapping):
            raise BackendError("app-server identity metadata has an invalid thread entry")
        validated = dict(entry)
        formal = validated.get("formal_thread_id")
        if not isinstance(formal, str) or not formal:
            raise BackendError("app-server identity metadata has duplicate or missing formal_thread_id")
        if any(not isinstance(validated.get(field), str) or not validated[field]
               for field in REQUIRED_IDENTITY):
            raise BackendError(f"app-server identity metadata is incomplete for {formal}")
        if validated.get("project_id_source") == "saved_project_readback":
            if validated.get("project_canonical_path") != validated.get("cwd"):
                raise BackendError(f"app-server project canonical path disagrees for {formal}")
            evidence = validated.get("project_identity_evidence")
            if (not isinstance(evidence, list) or not evidence
                    or any(not isinstance(item, str) or not item.strip() for item in evidence)):
                raise BackendError(f"app-server project readback evidence is incomplete for {formal}")
        return validated

    def _write_metadata(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        payload = {"threads": [dict(entry) for entry in entries.values()]}
        temporary = self.metadata_path.with_suffix(self.metadata_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.metadata_path)

    @staticmethod
    def _thread(result: Mapping[str, Any]) -> dict[str, Any]:
        thread = result.get("thread", result)
        if not isinstance(thread, Mapping):
            raise BackendError("app-server thread readback is malformed")
        return dict(thread)

    @staticmethod
    def _lifecycle(thread: Mapping[str, Any]) -> str:
        status = thread.get("status")
        if isinstance(status, Mapping):
            status = status.get("type")
        if status == "active":
            return "active"
        if status in {"idle", "notLoaded"}:
            return "completed"
        if status in {"systemError", "closed"}:
            return "failed"
        return "unknown"

    def _normalize(self, thread: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
        formal = thread.get("id")
        if not isinstance(formal, str) or formal != metadata["formal_thread_id"]:
            raise BackendError("app-server formal thread identity disagrees with metadata")
        for source, field in (("cwd", "cwd"), ("projectId", "project_id")):
            native_value = thread.get(source)
            if native_value is None and field == "project_id":
                if metadata.get("project_id_source") != "saved_project_readback":
                    raise BackendError("app-server projectId is unavailable; saved-project readback is required")
                native_value = metadata[field]
            if native_value != metadata[field]:
                raise BackendError(f"app-server {source} disagrees with controller metadata")
        return {
            **metadata,
            "client_thread_id": thread.get("sessionId"),
            "title": thread.get("name") or thread.get("preview"),
            "lifecycle": self._lifecycle(thread),
            "readback_evidence": [f"app-server:thread/read:{formal}"],
        }

    def _read_native(self, formal_thread_id: str) -> dict[str, Any]:
        return self._thread(self._result("thread/read", {"threadId": formal_thread_id, "includeTurns": False}))

    def _read_native_identity(self, formal_thread_id: str) -> dict[str, Any]:
        """Read identity from thread/read, enriching only from a live list readback.

        Some app-server versions omit projectId from thread/read while exposing
        it in thread/list. A saved-project sidecar does not need that native
        project membership field: once native cwd is present, `_normalize`
        cross-checks it against the saved-project canonical path. The list
        response remains a fallback for backends without saved-project evidence.
        """
        thread = self._read_native(formal_thread_id)
        metadata = self._metadata()
        entry = metadata.get(formal_thread_id)
        if (thread.get("cwd") is not None
                and entry is not None
                and entry.get("project_id_source") == "saved_project_readback"):
            return thread
        if thread.get("projectId") is not None and thread.get("cwd") is not None:
            return thread
        listed = self._result("thread/list", {"limit": 100})
        candidates = [
            item for item in listed.get("data", [])
            if isinstance(item, Mapping) and item.get("id") == formal_thread_id
        ]
        if len(candidates) != 1:
            raise BackendError("app-server identity list readback is not unique")
        listed_thread = dict(candidates[0])
        for field in ("cwd", "projectId"):
            read_value, list_value = thread.get(field), listed_thread.get(field)
            if read_value is not None and list_value is not None and read_value != list_value:
                raise BackendError(f"app-server {field} disagrees between thread/read and thread/list")
            if read_value is None:
                thread[field] = list_value
        return thread

    def capabilities(self) -> dict[str, Any]:
        self._initialize()
        metadata = self._metadata()
        if not metadata:
            raise BackendError("app-server identity metadata has no probe target")
        formal, entry = next(iter(metadata.items()))
        self._normalize(self._read_native_identity(formal), entry)
        return {
            "operations": [
                "create_thread", "list_tasks", "read_thread", "read_applied_route",
                "send_message_to_thread", "set_thread_archived", "read_archive_state",
            ],
            "formal_identity": True,
            "route_readback": True,
            "probe_target": {"formal_thread_id": formal, "host_id": entry["host_id"]},
            "evidence": ["app-server:initialize", f"app-server:metadata:{self.metadata_path}"],
        }

    def list_tasks(self, *, formal_thread_id: str | None = None, title_prefix: str | None = None,
                   client_thread_id: str | None = None, cursor: str | None = None, **_: Any) -> dict[str, Any]:
        metadata = self._metadata()
        if formal_thread_id and formal_thread_id not in metadata:
            return {"tasks": []}
        params: dict[str, Any] = {"cursor": cursor, "limit": 100, "searchTerm": title_prefix}
        if formal_thread_id and formal_thread_id in metadata:
            # App-server has no formal-thread filter. A cwd-scoped state-db
            # query keeps the reconciliation inventory complete and bounded.
            params["cwd"] = metadata[formal_thread_id]["cwd"]
            params["useStateDbOnly"] = True
        native = self._result("thread/list", params)
        tasks = []
        for item in native.get("data", []):
            if not isinstance(item, Mapping) or item.get("id") not in metadata:
                continue
            task = self._normalize(item, metadata[item["id"]])
            if formal_thread_id and task["formal_thread_id"] != formal_thread_id:
                continue
            if client_thread_id and task.get("client_thread_id") != client_thread_id:
                continue
            tasks.append(task)
        return {"tasks": tasks, "next_cursor": native.get("nextCursor")}

    def read_thread(self, *, formal_thread_id: str, host_id: str, **_: Any) -> dict[str, Any]:
        metadata = self._metadata()
        entry = metadata.get(formal_thread_id)
        if entry is None or entry["host_id"] != host_id:
            raise BackendError("app-server thread is absent from controller metadata")
        return self._normalize(self._read_native_identity(formal_thread_id), entry)

    def read_applied_route(self, *, formal_thread_id: str, host_id: str, **_: Any) -> dict[str, Any]:
        thread = self.read_thread(formal_thread_id=formal_thread_id, host_id=host_id)
        native = self._read_native_identity(formal_thread_id)
        model, effort = native.get("model"), native.get("reasoningEffort")
        if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
            raise BackendError("app-server thread readback lacks model or reasoningEffort")
        return {"model": model, "effort": effort,
                "evidence": [f"app-server:thread/read:{thread['formal_thread_id']}:route"]}

    def read_archive_state(self, *, formal_thread_id: str, host_id: str, **_: Any) -> dict[str, Any]:
        self.read_thread(formal_thread_id=formal_thread_id, host_id=host_id)
        archived_page = self._result("thread/list", {"archived": True, "limit": 100})
        archived = any(
            isinstance(item, Mapping) and item.get("id") == formal_thread_id
            for item in archived_page.get("data", [])
        )
        return {"archived": archived,
                "evidence": [f"app-server:thread/list:archived:{formal_thread_id}"]}

    def create_thread(self, *, dry_run: bool = False, **params: Any) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "evidence": ["app-server:thread/start:dry-run-contract"]}
        host_id = params.get("host_id") or self.host_id
        entry = {
            "formal_thread_id": "pending",
            "host_id": host_id,
            "task_id": params.get("task_id"),
            "run_id": params.get("run_id"),
            "attempt_id": params.get("attempt_id"),
            "owner_id": params.get("owner_id"),
            "cwd": params.get("cwd"),
            "project_id": params.get("project_id"),
            "project_id_source": params.get("project_id_source"),
            "project_canonical_path": params.get("project_canonical_path"),
            "project_identity_evidence": params.get("project_identity_evidence"),
        }
        try:
            self._validate_metadata_entry(entry)
        except BackendError as exc:
            if "is incomplete" in str(exc):
                raise BackendError("app-server create has incomplete controller identity") from exc
            raise BackendError(str(exc).replace("identity metadata", "create")) from exc
        # Read and validate the sidecar before creating native state.  If this
        # fails, no new thread has been opened.  The post-start fallback below
        # covers the narrower window in which persisting a new entry fails.
        metadata = self._metadata()
        start_params = {"cwd": entry["cwd"], "model": params.get("model")}
        if params.get("effort"):
            start_params["config"] = {"model_reasoning_effort": params["effort"]}
        if entry.get("project_id_source") != "saved_project_readback":
            start_params["projectId"] = entry["project_id"]
        thread = self._thread(self._result("thread/start", start_params))
        formal = thread.get("id")
        if not isinstance(formal, str) or not formal or not isinstance(host_id, str) or not host_id:
            raise BackendError("app-server create requires formal thread and configured host_id")
        entry["formal_thread_id"] = formal
        if formal in metadata:
            self._archive_unregistered_thread(formal)
            raise BackendError("app-server create formal thread already exists in metadata; new thread archived")
        metadata[formal] = entry
        try:
            self._write_metadata(metadata)
        except Exception as exc:
            try:
                self._archive_unregistered_thread(formal)
            except BackendError as cleanup_error:
                raise BackendError(
                    f"app-server create metadata persistence failed for {formal}; "
                    f"cleanup archive failed: {cleanup_error}"
                ) from cleanup_error
            raise BackendError(
                f"app-server create metadata persistence failed for {formal}; new thread archived"
            ) from exc
        return {"formal_thread_id": formal, "client_thread_id": thread.get("sessionId"),
                "host_id": host_id, "evidence": ["app-server:thread/start", "app-server:metadata:persisted"]}

    def _archive_unregistered_thread(self, formal_thread_id: str) -> None:
        """Archive and read back a thread created before its sidecar was durable."""
        self._result("thread/archive", {"threadId": formal_thread_id})
        archived_page = self._result("thread/list", {"archived": True, "limit": 100})
        archived = any(
            isinstance(item, Mapping) and item.get("id") == formal_thread_id
            for item in archived_page.get("data", [])
        )
        if not archived:
            raise BackendError(f"app-server cleanup archive readback failed for {formal_thread_id}")

    def adopt_thread(self, *, formal_thread_id: str, host_id: str, task_id: str,
                     run_id: str, attempt_id: str, owner_id: str, cwd: str,
                     project_id: str, identity_evidence: list[str] | None = None,
                     **_: Any) -> dict[str, Any]:
        """Enroll one pre-existing thread after a native readback.

        Adoption never derives managed identity from a title.  The caller must
        provide the controller identity and the bridge verifies native fields
        before writing the sidecar.
        """
        native = self._read_native_identity(formal_thread_id)
        thread = native
        if thread.get("projectId") is None:
            raise BackendError("app-server projectId is unavailable; adoption cannot be verified")
        if thread.get("id") != formal_thread_id or thread.get("cwd") != cwd or thread.get("projectId") != project_id:
            raise BackendError("adoption metadata disagrees with native thread readback")
        entry = {
            "formal_thread_id": formal_thread_id, "host_id": host_id,
            "task_id": task_id, "run_id": run_id, "attempt_id": attempt_id,
            "owner_id": owner_id, "cwd": cwd, "project_id": project_id,
            "identity_evidence": identity_evidence,
        }
        if any(not isinstance(entry[field], str) or not entry[field] for field in REQUIRED_IDENTITY):
            raise BackendError("adoption requires complete controller identity")
        if (not isinstance(identity_evidence, list) or not identity_evidence
                or any(not isinstance(item, str) or ":" not in item or not item.strip()
                       for item in identity_evidence)):
            raise BackendError("adoption requires external task-management identity evidence")
        metadata = self._metadata()
        existing = metadata.get(formal_thread_id)
        if existing is not None and existing != entry:
            raise BackendError("adoption would overwrite different identity metadata")
        metadata[formal_thread_id] = entry
        self._write_metadata(metadata)
        return {"formal_thread_id": formal_thread_id, "host_id": host_id,
                "identity_readback": self._normalize(thread, entry),
                "evidence": [f"app-server:thread/read:{formal_thread_id}", "app-server:metadata:enrolled"]}

    def send_message_to_thread(self, *, formal_thread_id: str, host_id: str, message: str | None = None,
                               dry_run: bool = False, **_: Any) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "evidence": ["app-server:turn/start:dry-run-contract"]}
        self.read_thread(formal_thread_id=formal_thread_id, host_id=host_id)
        result = self._result("turn/start", {"threadId": formal_thread_id,
                                               "input": [{"type": "text", "text": message or ""}]})
        turn = result.get("turn")
        turn_id = result.get("turnId")
        if isinstance(turn, Mapping):
            turn_id = turn_id or turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise BackendError("app-server turn/start returned no turn id")
        return {"thread_id": formal_thread_id, "turn_id": turn_id,
                "evidence": [f"app-server:turn/start:{formal_thread_id}:{turn_id}"]}

    def wait_for_turn_completion(self, formal_thread_id: str, turn_id: str, *,
                                 host_id: str | None = None,
                                 timeout: float | None = None, **_: Any) -> dict[str, Any]:
        """Wait for the completion event belonging to exactly one turn."""
        waiter = getattr(self.transport, "wait_for_notification", None)
        if not callable(waiter):
            raise BackendError("app-server transport cannot wait for turn notifications")
        return waiter("turn/completed", thread_id=formal_thread_id,
                       turn_id=turn_id, timeout=timeout)

    def read_history(self, formal_thread_id: str, turn_id: str, *, host_id: str | None = None, **_: Any) -> dict[str, Any]:
        """Read persisted turns for one completed turn, including rollout data."""
        result = self._result("thread/read", {
            "threadId": formal_thread_id, "includeTurns": True,
        })
        turns = result.get("turns")
        if turns is not None and isinstance(turns, list):
            matching = [turn for turn in turns if isinstance(turn, Mapping)
                        and turn.get("id") == turn_id]
            if not matching:
                # Let the probe's bounded persistence retry distinguish an
                # empty/incomplete read from a protocol-shaped response.
                return result
        return result

    def set_thread_archived(self, *, formal_thread_id: str, host_id: str, dry_run: bool = False,
                            **_: Any) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "evidence": ["app-server:thread/archive:dry-run-contract"]}
        self.read_thread(formal_thread_id=formal_thread_id, host_id=host_id)
        self._result("thread/archive", {"threadId": formal_thread_id})
        return {"evidence": [f"app-server:thread/archive:{formal_thread_id}"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--host-id", default="local")
    parser.add_argument("--app-server-command", nargs="+", default=["codex", "app-server", "--stdio"])
    args = parser.parse_args()
    bridge = AppServerBridge(args.app_server_command, args.metadata, host_id=args.host_id)
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                response = bridge.request(request["operation"], request.get("params"))
                print(json.dumps({"id": request.get("id"), "result": response}, ensure_ascii=False), flush=True)
            except (KeyError, TypeError, ValueError, BackendError) as exc:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
