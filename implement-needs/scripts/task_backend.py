"""Unified, fail-closed adapter for managed Codex task backends.

The controller deliberately depends on this small protocol instead of on a
particular desktop connector.  ``transport`` may be an object with methods,
or a mapping of operation names to callables.  For the app-server backend,
``method_map`` is mandatory: it must have been produced by a successful probe.
No JSON-RPC method name is inferred here.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from task_identity import TaskIdentity, format_title


class BackendError(RuntimeError):
    """A backend operation failed or did not provide trustworthy evidence."""


class ProtocolError(BackendError):
    """The selected backend cannot prove that it supports an operation."""


APP_SERVER = "codex-app-server-jsonrpc"
MCP_CONNECTOR = "mcp-task-connector"
REQUIRED_OPERATIONS = (
    "create_thread", "list_tasks", "read_thread", "read_applied_route",
    "send_message_to_thread", "set_thread_archived", "read_archive_state",
)


class JsonLineTransport:
    """Call a real local connector using one JSON request per line.

    The connector is deliberately an executable boundary.  It may be a small
    MCP/Desktop bridge, but it must return normalized task records and a
    capability receipt; this module never pretends that a CLI flag is a live
    connection.
    """

    def __init__(self, command: Sequence[str] | str, *, timeout: float = 10.0):
        argv = shlex.split(command, posix=False) if isinstance(command, str) else list(command)
        if not argv:
            raise ValueError("connector command is empty")
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.timeout = timeout
        self._lock = threading.Lock()
        self._request_id = 0

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> Any:
        with self._lock:
            if self.process.poll() is not None:
                raise BackendError("task connector exited before responding")
            self._request_id += 1
            request = {"id": self._request_id, "operation": operation, "params": dict(params or {})}
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            line = _readline_with_timeout(self.process.stdout, self.timeout)
            if not line:
                raise BackendError("task connector returned no response")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BackendError("task connector returned invalid JSON") from exc
            if response.get("id") not in (None, self._request_id):
                raise BackendError("task connector response id mismatch")
            return response

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class JsonRpcStdioTransport:
    """JSON-RPC stdio transport for an app-server method map from a probe."""

    def __init__(self, command: Sequence[str] | str, *, timeout: float = 10.0):
        argv = shlex.split(command, posix=False) if isinstance(command, str) else list(command)
        if not argv:
            raise ValueError("app-server command is empty")
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.timeout = timeout
        self._lock = threading.Lock()
        self._request_id = 0

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        with self._lock:
            if self.process.poll() is not None:
                raise BackendError("app-server exited before responding")
            self._request_id += 1
            request = {"jsonrpc": "2.0", "id": self._request_id,
                       "method": method, "params": dict(params or {})}
            assert self.process.stdin is not None and self.process.stdout is not None
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            while True:
                line = _readline_with_timeout(self.process.stdout, self.timeout)
                if not line:
                    raise BackendError("app-server returned no JSON-RPC response")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BackendError("app-server returned invalid JSON") from exc
                # app-server can emit notifications while a request is in flight.
                # Only the response carrying our request id completes this call.
                if isinstance(response, Mapping) and response.get("id") == self._request_id:
                    return response

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class McpStdioTransport(JsonRpcStdioTransport):
    """Speak the MCP stdio protocol and expose normalized operation calls.

    The MCP server is required to publish a ``capabilities`` tool. Its result
    supplies the probe target and normalized backend capability claims; the
    remaining operations are invoked as real ``tools/call`` requests.
    """

    def __init__(self, command: Sequence[str] | str, *, timeout: float = 10.0):
        super().__init__(command, timeout=timeout)
        self._initialized = False
        self._tools: dict[str, str] = {}

    def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            if self.process.poll() is not None:
                raise BackendError("MCP server exited before notification")
            request = {
                "jsonrpc": "2.0",
                "method": method,
                "params": dict(params or {}),
            }
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        response = super().request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "implement-needs", "version": "1"},
            },
        )
        result = _jsonrpc_result(response, "MCP initialize")
        if not isinstance(result, Mapping):
            raise ProtocolError("MCP initialize returned no result object")
        self._notify("notifications/initialized")
        tools_response = super().request("tools/list", {})
        tools_result = _jsonrpc_result(tools_response, "MCP tools/list")
        tools = tools_result.get("tools") if isinstance(tools_result, Mapping) else None
        if not isinstance(tools, list):
            raise ProtocolError("MCP tools/list returned no tools array")
        for tool in tools:
            if not isinstance(tool, Mapping) or not isinstance(tool.get("name"), str):
                continue
            name = tool["name"]
            normalized = name.replace("-", "_").replace("/", "_")
            for operation in ("capabilities", *REQUIRED_OPERATIONS):
                if normalized == operation or normalized.endswith("_" + operation):
                    if operation in self._tools and self._tools[operation] != name:
                        raise ProtocolError(f"MCP exposes ambiguous tool for {operation}")
                    self._tools[operation] = name
        missing = sorted({"capabilities", *REQUIRED_OPERATIONS} - set(self._tools))
        if missing:
            raise ProtocolError("MCP tools/list is missing tools: " + ", ".join(missing))
        self._initialized = True

    def request(self, operation: str, params: Mapping[str, Any] | None = None) -> Any:
        self._ensure_initialized()
        tool_name = self._tools.get(operation)
        if tool_name is None:
            raise ProtocolError(f"MCP operation was not advertised: {operation}")
        response = super().request(
            "tools/call", {"name": tool_name, "arguments": dict(params or {})}
        )
        result = _jsonrpc_result(response, f"MCP tools/call {operation}")
        return _mcp_tool_result(result, operation)


def _readline_with_timeout(stream: Any, timeout: float) -> str:
    result: list[str] = []
    error: list[BaseException] = []

    def read() -> None:
        try:
            result.append(stream.readline())
        except (OSError, ValueError) as exc:  # pragma: no cover - OS failure path
            error.append(exc)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise BackendError("task backend response timed out")
    if error:
        raise BackendError("task backend response could not be read") from error[0]
    return result[0] if result else ""


def _jsonrpc_result(response: Any, operation: str) -> Any:
    if not isinstance(response, Mapping):
        raise BackendError(f"{operation} returned a non-object response")
    if response.get("error") is not None:
        raise BackendError(f"{operation} failed: {response['error']}")
    return response.get("result", response)


def _mcp_tool_result(result: Any, operation: str) -> Any:
    if not isinstance(result, Mapping):
        raise BackendError(f"MCP {operation} returned no result object")
    if result.get("isError") is True:
        raise BackendError(f"MCP {operation} returned isError")
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, Mapping) or item.get("type") != "text":
                continue
            try:
                value = json.loads(str(item.get("text", "")))
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                return dict(value)
    raise BackendError(f"MCP {operation} returned no structured JSON result")


def probe_connector(transport: Any) -> dict[str, Any]:
    """Run a live, non-mutating capability probe for every logical operation.

    Read operations run against one existing probe target. Mutating operations
    receive ``dry_run`` and must explicitly acknowledge that no state changed.
    This makes a capability receipt an operation-level fact rather than a list of
    names returned by the connector.
    """
    adapter = TaskBackend(MCP_CONNECTOR, transport)
    payload = _mapping(adapter.call("capabilities", {}))
    operations = payload.get("operations")
    if not isinstance(operations, (list, tuple, set)):
        raise ProtocolError("connector capabilities have no operations list")
    missing = sorted(set(REQUIRED_OPERATIONS) - set(operations))
    if missing:
        raise ProtocolError("connector is missing operations: " + ", ".join(missing))
    if payload.get("formal_identity") is not True or payload.get("route_readback") is not True:
        raise ProtocolError("connector did not prove formal identity and route readback")
    target = payload.get("probe_target") if isinstance(payload.get("probe_target"), Mapping) else {}
    formal = target.get("formal_thread_id") or target.get("formalThreadId")
    host = target.get("host_id") or target.get("hostId")
    if formal and not host:
        inventory = list_tasks(adapter, formal_thread_id=formal, page_limit=1)
        if inventory.get("reconciliation_status") != "complete":
            raise ProtocolError("probe target discovery is inconclusive")
        candidates = [task for task in inventory["tasks"] if isinstance(task, Mapping)]
        if len(candidates) != 1:
            raise ProtocolError("probe target discovery is not unique")
        host = _first(candidates[0], "host_id", "hostId")
    if not formal or not host:
        raise ProtocolError("connector capabilities have no formal probe target")

    inventory = list_tasks(adapter, formal_thread_id=str(formal), page_limit=1)
    if inventory.get("reconciliation_status") != "complete":
        raise ProtocolError("probe target listing is inconclusive")
    listed = [task for task in inventory.get("tasks", []) if isinstance(task, Mapping)]
    if not any(
        (_first(task, "formal_thread_id", "formalThreadId", "thread_id", "threadId") == formal)
        and (_first(task, "host_id", "hostId") == host)
        for task in listed
    ):
        raise ProtocolError("probe target is absent from list_tasks readback")
    identity = read_task(adapter, str(formal), str(host))
    required_identity = ("task_id", "run_id", "attempt_id", "owner_id", "cwd", "project_id")
    missing_identity = [name for name in required_identity if not identity.get(name)]
    if missing_identity:
        raise ProtocolError("probe identity readback is incomplete: " + ", ".join(missing_identity))
    route = read_applied_route(adapter, str(formal), str(host))
    archive = read_archive_state(adapter, str(formal), str(host))
    for operation, params in (
        ("create_thread", {"dry_run": True, "probe": True}),
        ("send_message_to_thread", {"dry_run": True, "probe": True, "formal_thread_id": formal, "host_id": host}),
        ("set_thread_archived", {"dry_run": True, "probe": True, "formal_thread_id": formal, "host_id": host, "archived": True}),
    ):
        result = _mapping(adapter.call(operation, params))
        if result.get("dry_run") is not True:
            raise ProtocolError(f"{operation} did not acknowledge dry_run")
    evidence = list(payload.get("evidence", [])) + [
        "live_capabilities", "probe:list_tasks", "probe:read_thread",
        "probe:read_applied_route", "probe:read_archive_state",
        "probe:create_thread:dry_run", "probe:send_message_to_thread:dry_run",
        "probe:set_thread_archived:dry_run",
    ]
    return {"decision": "allow", "backend": MCP_CONNECTOR,
            "operations": list(REQUIRED_OPERATIONS),
            "probe_target": {"formal_thread_id": formal, "host_id": host},
            "identity_readback": identity, "route_readback": route,
            "archive_readback": archive, "capability_evidence": evidence}


def probe_app_server(transport: Any, method_map: Mapping[str, str]) -> dict[str, Any]:
    """Probe every app-server operation against one existing task target."""
    if not method_map or "initialize" not in method_map:
        raise ProtocolError("app-server probe did not provide initialize")
    missing = sorted(set(REQUIRED_OPERATIONS) - set(method_map))
    if missing:
        raise ProtocolError("app-server method map is incomplete: " + ", ".join(missing))
    if any(not isinstance(method_map.get(name), str) or not method_map[name].strip()
           for name in ("initialize", *REQUIRED_OPERATIONS)):
        raise ProtocolError("app-server method map contains an empty method")
    target = method_map.get("probe_target")
    if not isinstance(target, Mapping):
        raise ProtocolError("app-server probe requires probe_target")
    formal = _first(target, "formal_thread_id", "formalThreadId")
    host = _first(target, "host_id", "hostId")
    if not formal or not host:
        raise ProtocolError("app-server probe target lacks formal identity")

    initialize = _jsonrpc_result(
        transport.request(
            method_map["initialize"],
            {
                "clientInfo": {"name": "implement-needs", "version": "1"},
                "capabilities": {},
            },
        ),
        "app-server initialize",
    )
    if not isinstance(initialize, Mapping):
        raise ProtocolError("app-server initialize did not return an object")
    adapter = TaskBackend(APP_SERVER, transport, method_map)
    inventory = list_tasks(adapter, formal_thread_id=str(formal), page_limit=1)
    if inventory.get("reconciliation_status") != "complete":
        raise ProtocolError("app-server list_tasks probe is inconclusive")
    listed = [task for task in inventory.get("tasks", []) if isinstance(task, Mapping)]
    if not any(
        _first(task, "formal_thread_id", "formalThreadId", "thread_id", "threadId") == formal
        and _first(task, "host_id", "hostId") == host
        for task in listed
    ):
        raise ProtocolError("app-server probe target is absent from list_tasks")
    identity = read_task(adapter, str(formal), str(host))
    route = read_applied_route(adapter, str(formal), str(host))
    archive = read_archive_state(adapter, str(formal), str(host))
    for operation, params in (
        ("create_thread", {"dry_run": True, "probe": True}),
        ("send_message_to_thread", {"dry_run": True, "probe": True,
                                     "formal_thread_id": formal, "host_id": host}),
        ("set_thread_archived", {"dry_run": True, "probe": True,
                                  "formal_thread_id": formal, "host_id": host,
                                  "archived": True}),
    ):
        result = _mapping(adapter.call(operation, params))
        if result.get("dry_run") is not True:
            raise ProtocolError(f"app-server {operation} did not acknowledge dry_run")
    methods = {key: value for key, value in method_map.items() if key != "probe_target"}
    return {"decision": "allow", "backend": APP_SERVER, "method_map": methods,
            "probe_target": {"formal_thread_id": formal, "host_id": host},
            "identity_readback": identity, "route_readback": route,
            "archive_readback": archive,
            "capability_evidence": [
                "initialize_response", "probe:list_tasks", "probe:read_thread",
                "probe:read_applied_route", "probe:read_archive_state",
                "probe:create_thread:dry_run", "probe:send_message_to_thread:dry_run",
                "probe:set_thread_archived:dry_run",
            ]}


@dataclass
class TaskBackend:
    """Dispatch an operation through a selected and already-probed backend."""

    name: str
    transport: Any
    method_map: Mapping[str, str] | None = None

    def call(self, operation: str, params: Mapping[str, Any] | None = None) -> Any:
        params = dict(params or {})
        if self.name == APP_SERVER:
            if not self.method_map or operation not in self.method_map:
                raise ProtocolError(f"unconfirmed app-server operation: {operation}")
            method = self.method_map[operation]
            request = getattr(self.transport, "request", None)
            if request is None:
                raise ProtocolError("app-server transport has no request method")
            response = request(method, params)
        elif self.name == MCP_CONNECTOR:
            request = getattr(self.transport, "request", None)
            if request is None:
                raise ProtocolError("MCP connector transport has no request method")
            response = request(operation, params)
        else:
            target = self.transport.get(operation) if isinstance(self.transport, Mapping) else getattr(self.transport, operation, None)
            if target is None or not callable(target):
                raise ProtocolError(f"backend does not implement operation: {operation}")
            response = target(**params)
        if isinstance(response, Mapping) and response.get("error") is not None:
            raise BackendError(str(response["error"]))
        if isinstance(response, Mapping) and "result" in response:
            return response["result"]
        return response


def _adapter(backend: TaskBackend | Any) -> TaskBackend:
    if isinstance(backend, TaskBackend):
        return backend
    if isinstance(backend, Mapping):
        return TaskBackend(str(backend.get("name", "host-task-api")), backend, backend.get("method_map"))
    return TaskBackend(getattr(backend, "name", "host-task-api"), backend, getattr(backend, "method_map", None))


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    raise BackendError("backend returned a non-object response")


def _first(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if value.get(name) is not None:
            return value[name]
    return None


def create_bootstrap_task(
    backend: TaskBackend | Any,
    run_id: str,
    task_identity: TaskIdentity,
    description: str,
    model: str,
    effort: str,
    *,
    owner_id: str | None = None,
    cwd: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Create a task and return creation plus formal identity evidence.

    A create response is never treated as route or identity proof.  Callers
    must perform the returned formal readback before crossing the bootstrap
    barrier.
    """
    title = format_title(task_identity, description)
    adapter = _adapter(backend)
    result = _mapping(adapter.call("create_thread", {
        "run_id": run_id, "task_id": task_identity.task_id,
        "attempt_id": task_identity.attempt_id, "title": title,
        "description": description, "model": model, "effort": effort,
        "owner_id": owner_id, "cwd": cwd, "project_id": project_id,
    }))
    formal = _first(result, "formal_thread_id", "formalThreadId", "thread_id", "threadId")
    host = _first(result, "host_id", "hostId")
    client = _first(result, "client_thread_id", "clientThreadId")
    evidence = list(result.get("evidence", []))
    evidence.append("create_response")
    identity_readback = None
    if formal is not None and host is not None:
        identity_readback = read_task(adapter, formal, host)
        if (identity_readback["formal_thread_id"], identity_readback["host_id"]) != (formal, host):
            raise BackendError("formal identity readback disagrees with create response")
        evidence.extend(identity_readback.get("readback_evidence", []))
    else:
        evidence.append("formal_identity_unavailable")
    return {"client_thread_id": client, "formal_thread_id": formal,
            "host_id": host, "title": title,
            "readback_evidence": evidence, "identity_readback": identity_readback,
            "creation_response": result}


def list_tasks(
    backend: TaskBackend | Any, time_window: Any = None,
    title_prefix: str | None = None, client_thread_id: str | None = None,
    formal_thread_id: str | None = None, page_limit: int = 100,
) -> dict[str, Any]:
    """List a bounded inventory; incomplete pagination is inconclusive."""
    if page_limit < 1:
        raise ValueError("page_limit must be positive")
    adapter, tasks, cursor = _adapter(backend), [], None
    for _ in range(page_limit):
        params = {"time_window": time_window, "title_prefix": title_prefix,
                  "client_thread_id": client_thread_id,
                  "formal_thread_id": formal_thread_id}
        if cursor is not None:
            params["cursor"] = cursor
        result = _mapping(adapter.call("list_tasks", params))
        page = result.get("tasks", result.get("threads", []))
        if not isinstance(page, list):
            raise BackendError("task listing has no list-shaped tasks field")
        tasks.extend(page)
        cursor = _first(result, "next_cursor", "nextCursor")
        if not cursor:
            return {"tasks": tasks, "reconciliation_status": "complete"}
    return {"tasks": tasks, "reconciliation_status": "inconclusive", "next_cursor": cursor}


def read_task(backend: TaskBackend | Any, formal_thread_id: str, host_id: str) -> dict[str, Any]:
    result = _mapping(_adapter(backend).call("read_thread", {"formal_thread_id": formal_thread_id, "host_id": host_id}))
    readback_formal = _first(result, "formal_thread_id", "formalThreadId", "thread_id", "threadId")
    readback_host = _first(result, "host_id", "hostId")
    if not readback_formal or not readback_host:
        raise BackendError("formal thread readback is missing formal_thread_id or host_id")
    if (readback_formal, readback_host) != (formal_thread_id, host_id):
        raise BackendError("formal thread readback disagrees with requested identity")
    raw_evidence = result.get("readback_evidence", result.get("evidence", []))
    if not isinstance(raw_evidence, list) or not raw_evidence or any(not isinstance(item, str) or not item.strip() for item in raw_evidence):
        raise BackendError("formal thread readback is missing backend evidence")
    lifecycle = result.get("lifecycle")
    if not isinstance(lifecycle, str) or not lifecycle.strip():
        raise BackendError("formal thread readback is missing lifecycle")
    return {**result, "formal_thread_id": readback_formal,
            "host_id": readback_host,
            "readback_evidence": raw_evidence + ["formal_thread_readback"]}


def read_applied_route(backend: TaskBackend | Any, formal_thread_id: str, host_id: str) -> dict[str, Any]:
    result = _mapping(_adapter(backend).call("read_applied_route", {"formal_thread_id": formal_thread_id, "host_id": host_id}))
    model, effort = _first(result, "model", "applied_model"), _first(result, "effort", "reasoning_effort", "applied_effort", "thinking")
    if not model or not effort:
        raise BackendError("route readback is missing model or effort")
    evidence = list(result.get("readback_evidence", result.get("evidence", [])))
    if not evidence:
        raise BackendError("route readback is missing backend evidence")
    return {"model": model, "effort": effort, "evidence": evidence + ["configured_route_readback"]}


def send_assignment(backend: TaskBackend | Any, formal_thread_id: str, host_id: str, message: str) -> dict[str, Any]:
    result = _mapping(_adapter(backend).call("send_message_to_thread", {"formal_thread_id": formal_thread_id, "host_id": host_id, "message": message}))
    return {**result, "evidence": list(result.get("evidence", [])) + ["assignment_sent"]}


def archive_task(backend: TaskBackend | Any, formal_thread_id: str, host_id: str) -> dict[str, Any]:
    result = _mapping(_adapter(backend).call("set_thread_archived", {"formal_thread_id": formal_thread_id, "host_id": host_id, "archived": True}))
    return {**result, "operation_evidence": list(result.get("operation_evidence", result.get("evidence", []))) + ["archive_requested"]}


def read_archive_state(backend: TaskBackend | Any, formal_thread_id: str, host_id: str) -> dict[str, Any]:
    result = _mapping(_adapter(backend).call("read_archive_state", {"formal_thread_id": formal_thread_id, "host_id": host_id}))
    archived = result.get("archived")
    if not isinstance(archived, bool):
        raise BackendError("archive readback is missing boolean archived state")
    evidence = list(result.get("readback_evidence", result.get("evidence", [])))
    if not evidence:
        raise BackendError("archive readback is missing backend evidence")
    return {"archived": archived, "readback_evidence": evidence + ["archive_readback"]}
