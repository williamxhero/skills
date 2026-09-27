from __future__ import annotations

import time
from pathlib import Path

import pytest

from spec_runner import lease_runtime, workflow
from spec_runner.errors import RunnerError


class _FailedHeartbeatStore:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def heartbeat_lease(self, *, scope: str, owner_token: str) -> None:
        raise RunnerError("writer_lease_lost", "lease row disappeared")

    def close(self) -> None:
        return None


def test_heartbeat_failure_reaches_the_active_run_health_signal(monkeypatch, tmp_path: Path) -> None:
    run_id = "lease-health-test"

    def open_failed(_control_root: Path, *, create: bool) -> _FailedHeartbeatStore:
        return _FailedHeartbeatStore()

    monkeypatch.setattr(workflow.Store, "open", staticmethod(open_failed))
    stop, thread = workflow._start_lease_heartbeat(
        control_root=tmp_path,
        scope="repo@HEAD",
        owner_token="owner",
        run_id=run_id,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                lease_runtime.check(run_id)
            except RunnerError as exc:
                assert exc.code == "writer_lease_lost"
                assert exc.details["source_code"] == "writer_lease_lost"
                break
            time.sleep(0.02)
        else:
            pytest.fail("heartbeat failure was not published to the active run")
    finally:
        stop.set()
        thread.join(timeout=2)
        lease_runtime.unregister(run_id)
