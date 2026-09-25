from __future__ import annotations

import json

import pytest

from spec_runner.continuation import build_bundle, read_bundle, write_bundle_atomic
from spec_runner.errors import RunnerError


def bundle_input():
    return {
        "run_id": "run-1", "spec_key": "S1", "stage": "implement", "generation": 3,
        "input_revision": "input-digest", "requirements": [{"text": "finish"}],
        "confirmed_decisions": [{"id": "d1", "value": "keep workspace"}],
        "tickets": [{"key": "S1.1", "state": "open"}], "dependencies": [],
        "workspace": {"path": "worktree", "branch": "runner/S1", "head": "abc"},
        "verified_items": [{"id": "tests", "receipt": "r1"}],
        "remaining_items": [{"id": "review"}], "tests": [{"command": ["pytest"], "passed": True}],
        "review": [], "unconfirmed_operations": [{"id": "github:pr", "state": "unknown"}],
        "authorization": {"repository": "owner/repo", "write_scope": ["fixture"]},
        "last_verified_progress": "tests-pass", "source_refs": [{"kind": "git", "ref": "abc"}],
    }


def test_bundle_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "control" / "continuation.json"
    written = write_bundle_atomic(path, build_bundle(bundle_input()))
    assert path.is_file()
    assert read_bundle(path).public() == written
    assert "encrypted_content" not in json.dumps(written)


def test_bundle_rejects_hidden_or_encrypted_history():
    document = bundle_input()
    document["requirements"] = [{"encrypted_content": "opaque"}]
    with pytest.raises(RunnerError) as error:
        build_bundle(document)
    assert error.value.code == "continuation_forbidden_material"


def test_bundle_digest_and_identity_are_verified():
    document = build_bundle(bundle_input()).public()
    document["remaining_items"] = [{"id": "changed"}]
    with pytest.raises(RunnerError) as error:
        build_bundle(document)
    assert error.value.code == "continuation_digest_mismatch"
