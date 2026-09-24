from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from spec_runner.errors import RunnerError
from spec_runner.takeover import completion_action, inspect_takeover, inventory_from_thread_observation, perform_cleanup


def _repo(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Acceptance"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "acceptance@example.invalid"], check=True)
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
    return repository


def _observation() -> dict[str, object]:
    return {
        "schema_version": "spec-runner-sdk-thread-inspection/v1",
        "thread_id": "acceptance-source",
        "thread": {"forked_from_id": None},
        "business_items": [{"turn_id": "turn-1", "item": {"type": "userMessage", "id": "item-1", "content": [{"type": "text", "text": "finish the fixture"}]}}],
        "completeness": {"state": "complete", "reasons": []},
    }


def test_takeover_identity_is_stable_and_sensitive_paths_are_not_reported(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    (repository / "fixture").mkdir()
    (repository / "fixture" / "partial.py").write_text("partial\n", encoding="utf-8")
    (repository / "config").mkdir()
    (repository / "config" / "service-secret.txt").write_text("do-not-report\n", encoding="utf-8")

    first = inventory_from_thread_observation(observation=_observation(), repository=repository, scope=["fixture"])
    second = inventory_from_thread_observation(observation=_observation(), repository=repository, scope=["fixture"])

    assert first["facts"]["working_tree"] == second["facts"]["working_tree"]
    assert "config/service-secret.txt" not in first["facts"]["working_tree"]["changed"]
    assert first["facts"]["working_tree"]["redacted_path_count"] == 1


def test_historical_delivery_claim_cannot_authorize_cleanup(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    report = inspect_takeover({
        "schema_version": "spec-runner-takeover-input/v1",
        "repository_path": str(repository),
        "source_threads": [],
        "artifacts": [],
        "facts": {"merged": True, "verification_receipt": {"candidate_sha": "forged"}},
    })

    assert completion_action(report)["state"] == "reverify_delivery"
    with pytest.raises(RunnerError, match="authoritative"):
        perform_cleanup({**report, "historical_facts": {"merged": True, "verification_receipt": {"candidate_sha": "forged"}}})


def test_durable_handover_readback_releases_a_complete_source_thread(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    evidence = {
        "schema_version": "spec-runner-sdk-thread-interrupt/v1",
        "thread_id": "source-thread",
        "accepted": True,
        "source_writer_state": "stopped",
        "dispatcher_state": "quiesced",
        "readback": {"source_thread_id": "source-thread", "observed_status": "completed"},
        "evidence_limits": {
            "source_stop_confirmed": True,
            "dispatcher_quiesced": True,
            "ownership_transferred": True,
        },
    }
    report = inspect_takeover({
        "schema_version": "spec-runner-takeover-input/v1",
        "repository_path": str(repository),
        "source_threads": [{
            "id": "source-thread",
            "ownership": "unknown",
            "active": True,
            "observation": _observation(),
            "handover_evidence": evidence,
        }],
        "artifacts": [],
        "facts": {"requirements": ["finish the fixture"]},
    })

    assert report["next_state"] == "adopted_ready"
    assert report["unresolved"] == []
    assert report["adopted_threads"][0]["state"] == "released"
