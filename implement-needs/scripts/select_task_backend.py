"""Select the first usable child-task backend and emit a deterministic receipt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-task-api", action="store_true")
    parser.add_argument("--mcp-task-connector", action="store_true")
    parser.add_argument("--app-server-probe", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.host_task_api:
        backend, evidence = "host-task-api", ["host_task_api:available"]
    elif args.mcp_task_connector:
        backend, evidence = "mcp-task-connector", ["mcp_task_connector:available"]
    else:
        probe = {}
        if args.app_server_probe:
            try:
                probe = json.loads(args.app_server_probe.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                probe = {}
        if probe.get("decision") == "allow" and probe.get("available") is True:
            backend, evidence = "codex-app-server-jsonrpc", ["app_server_probe:allow"]
        else:
            backend, evidence = "unblock-development", ["all_task_backends:unavailable"]
    payload = {"schema_version": 1, "backend": backend, "evidence": evidence,
               "decision": "allow" if backend != "unblock-development" else "repair"}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["decision"] == "allow" else 1


if __name__ == "__main__":
    raise SystemExit(main())
