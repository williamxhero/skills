"""Select a live managed-task backend and emit its capability receipt."""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from task_backend import (
    JsonLineTransport,
    JsonRpcStdioTransport,
    McpStdioTransport,
    ProtocolError,
    probe_app_server,
    probe_connector,
)


def _command(value: str | None) -> list[str] | None:
    return shlex.split(value, posix=False) if value else None


def _probe(args: argparse.Namespace) -> dict:
    # Compatibility flags remain accepted, but they no longer claim a backend
    # is live. A command must answer the capability probe.
    if args.host_task_api_command:
        command = _command(args.host_task_api_command)
        transport = JsonLineTransport(command or [], timeout=args.timeout)
        try:
            result = probe_connector(transport)
            result["backend"] = "host-task-api"
            return result
        finally:
            transport.close()
    if args.mcp_task_connector or args.mcp_command:
        command = _command(args.mcp_command)
        if not command:
            raise ProtocolError("mcp-task-connector requires --mcp-command")
        transport_class = McpStdioTransport if args.mcp_protocol == "mcp-stdio" else JsonLineTransport
        transport = transport_class(command, timeout=args.timeout)
        try:
            return probe_connector(transport)
        finally:
            transport.close()
    if args.app_server_command:
        if args.app_server_metadata:
            from app_server_bridge import AppServerBridge
            transport = AppServerBridge(
                _command(args.app_server_command) or [], args.app_server_metadata,
            )
            try:
                return probe_connector(transport)
            finally:
                transport.close()
        if not args.method_map:
            raise ProtocolError("app-server requires --method-map JSON")
        method_map = json.loads(args.method_map.read_text(encoding="utf-8"))
        transport = JsonRpcStdioTransport(_command(args.app_server_command) or [], timeout=args.timeout)
        try:
            return probe_app_server(transport, method_map)
        finally:
            transport.close()
    if args.app_server_probe:
        raise ProtocolError("offline app-server probe receipts cannot authorize a live backend")
    raise ProtocolError("no live task backend capability receipt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-task-api", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-task-api-command", help="command for the live Host Task API bridge")
    parser.add_argument("--mcp-task-connector", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mcp-command", help="command for the live MCP/Desktop task bridge")
    parser.add_argument("--mcp-protocol", choices=("json-lines", "mcp-stdio"), default="json-lines")
    parser.add_argument("--app-server-command", help="command for codex app-server --stdio")
    parser.add_argument("--app-server-metadata", type=Path,
                        help="controller-owned identity metadata for the native app-server bridge")
    parser.add_argument("--method-map", type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--app-server-probe", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = _probe(args)
        payload = {"schema_version": 2, **result,
                   "evidence": result.get("capability_evidence", []), "decision": "allow"}
        code = 0
    except (OSError, UnicodeError, json.JSONDecodeError, ProtocolError, ValueError) as exc:
        payload = {"schema_version": 2, "backend": "unblock-development",
                   "evidence": ["all_task_backends:unavailable", str(exc)], "decision": "repair"}
        code = 1
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
