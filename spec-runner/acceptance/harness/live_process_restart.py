"""Verify same-thread recovery across two real SDK worker processes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from spec_runner.codex_adapter import CodexAdapter


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _record_turn(manifest_path: Path, thread_id: str, turn_id: str) -> None:
    manifest = _read(manifest_path)
    resources = manifest.setdefault("resources", {})
    threads = resources.setdefault("threads", [])
    if thread_id not in threads:
        threads.append(thread_id)
    manifest["active_thread_id"] = thread_id
    manifest["active_turn_id"] = turn_id
    _write(manifest_path, manifest)


def _run_phase(manifest_path: Path, model: str, effort: str, phase: str) -> None:
    manifest = _read(manifest_path)
    if manifest.get("repository") != "williamxhero/skills":
        raise ValueError("probe requires the authorized repository")
    repository = Path(str(manifest["repository_path"])).resolve(strict=True)
    marker = str(manifest["run_marker"])
    schema = {
        "type": "object",
        "properties": {"phase": {"type": "string"}, "status": {"type": "string"}},
        "required": ["phase", "status"],
        "additionalProperties": False,
    }
    if phase == "initial":
        thread_id = None
        task = f"For live process-restart acceptance {marker}, return JSON with phase=initial and status=completed. Do not run tools or change files."
    elif phase == "resume":
        thread_id = str(manifest.get("active_thread_id") or "")
        if not thread_id:
            raise ValueError("resume phase requires the initial durable thread identity")
        task = f"For live process-restart acceptance {marker}, continue on this existing thread after the worker process restarted. Return JSON with phase=resume and status=completed. Do not run tools or change files."
    else:
        raise ValueError(f"unknown phase: {phase}")

    result = CodexAdapter().run_semantic(
        phase="grill",
        repository_path=repository,
        model=model,
        effort=effort,
        trusted={"task": task, "run_marker": marker},
        untrusted={},
        schema=schema,
        thread_id=thread_id,
        on_turn_started=lambda observed_thread, turn: _record_turn(manifest_path, observed_thread, turn),
    )
    manifest = _read(manifest_path)
    phases = manifest.setdefault("process_phases", {})
    phases[phase] = {
        "pid": os.getpid(),
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "status": result.status,
        "result": result.public(),
    }
    _write(manifest_path, manifest)
    if phase == "initial":
        # The parent intentionally starts a fresh interpreter for the next phase.
        os._exit(0)


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_live_sdk_process_restart":
        raise AssertionError("report must identify the live SDK process-restart evidence")
    phases = report.get("process_phases")
    if not isinstance(phases, dict) or not isinstance(phases.get("initial"), dict) or not isinstance(phases.get("resume"), dict):
        raise AssertionError("report must contain both process phases")
    initial = phases["initial"]
    resumed = phases["resume"]
    if initial.get("status") != "completed" or resumed.get("status") != "completed":
        raise AssertionError("both live SDK turns must complete")
    if initial.get("thread_id") != resumed.get("thread_id"):
        raise AssertionError("resume must preserve the SDK thread identity")
    if initial.get("turn_id") == resumed.get("turn_id"):
        raise AssertionError("resume must create a distinct SDK turn")
    if initial.get("pid") == resumed.get("pid"):
        raise AssertionError("phases must run in different worker processes")
    archive = report.get("archive_readback")
    if not isinstance(archive, dict) or archive.get("archived") is not True or archive.get("thread_id") != initial.get("thread_id"):
        raise AssertionError("the resumed thread must be archived and read back")


def run_probe(manifest_path: Path, model: str, effort: str) -> dict[str, Any]:
    manifest = _read(manifest_path)
    script = Path(__file__).resolve()
    command = [sys.executable, str(script), "--manifest", str(manifest_path), "--model", model, "--effort", effort, "--phase", "initial"]
    first = subprocess.run(command, cwd=manifest["repository_path"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if first.returncode != 0:
        raise RuntimeError("initial live SDK worker failed")
    command[-1] = "resume"
    second = subprocess.run(command, cwd=manifest["repository_path"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if second.returncode != 0:
        raise RuntimeError("resumed live SDK worker failed")
    manifest = _read(manifest_path)
    thread_id = str(manifest.get("active_thread_id") or "")
    archive = CodexAdapter().archive_and_readback(thread_id=thread_id, repository_path=Path(manifest["repository_path"]))
    report = {
        "schema_version": "spec-runner-live-sdk-process-restart/v1",
        "run_marker": manifest["run_marker"],
        "evidence_kind": "observed_live_sdk_process_restart",
        "sdk_version": "0.155.1",
        "repository": manifest["repository"],
        "process_phases": manifest.get("process_phases", {}),
        "archive_readback": archive,
        "worker_exit_codes": {"initial": first.returncode, "resume": second.returncode},
        "unverified": [
            "native Windows detached-parent exit",
            "hybrid real SDK fault injection",
            "source-thread takeover",
            "GitHub merge queue",
            "complete A-J recovery acceptance",
        ],
    }
    validate_report(report)
    report_path = manifest_path.parent.parent.parent / "reports" / f"{manifest['run_marker']}-live-process-restart.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--phase", choices=("initial", "resume"))
    args = parser.parse_args()
    if args.phase:
        _run_phase(args.manifest, args.model, args.effort, args.phase)
        return 0
    report = run_probe(args.manifest, args.model, args.effort)
    print(json.dumps({"report": str(args.manifest.parent.parent.parent / "reports" / f"{report['run_marker']}-live-process-restart.json"), "thread_id": report["archive_readback"]["thread_id"], "passed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())