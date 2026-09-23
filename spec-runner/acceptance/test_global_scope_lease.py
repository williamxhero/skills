from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.errors import RunnerError
from spec_runner.workflow import _acquire_global_lease, _release_global_lease


def test_different_control_roots_cannot_claim_same_repository_scope():
    scope = f"acceptance-global-lease-{uuid.uuid4()}"
    first = _acquire_global_lease(scope=scope, owner_token="owner-a", stale_after_seconds=60)
    try:
        with pytest.raises(RunnerError) as error:
            _acquire_global_lease(scope=scope, owner_token="owner-b", stale_after_seconds=60)
        assert error.value.code == "global_writer_busy"
    finally:
        _release_global_lease(first, "owner-a")
    assert not first.exists()
