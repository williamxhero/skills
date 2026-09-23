from __future__ import annotations

import sys
import uuid
import os
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.errors import RunnerError
from spec_runner.workflow import _acquire_global_lease, _release_global_lease
from spec_runner import workflow
from spec_runner.scope_lock import ScopeLock


@pytest.fixture
def lock_root(monkeypatch):
    runtime = Path(__file__).parent / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runtime, prefix="scope-lock-") as directory:
        root = Path(directory)
        monkeypatch.setattr(workflow, "_global_lease_path", lambda scope: root / "scope.json")
        yield root


def test_different_control_roots_cannot_claim_same_repository_scope(lock_root):
    scope = f"acceptance-global-lease-{uuid.uuid4()}"
    first = _acquire_global_lease(scope=scope, owner_token="owner-a", stale_after_seconds=60)
    try:
        with pytest.raises(RunnerError) as error:
            _acquire_global_lease(scope=scope, owner_token="owner-b", stale_after_seconds=60)
        assert error.value.code == "global_writer_busy"
    finally:
        _release_global_lease(first, "owner-a")
    assert first.descriptor is None
    # The inode stays; deleting it would allow simultaneous locks on two inodes.
    assert first.path.exists()
    second = _acquire_global_lease(scope=scope, owner_token="owner-b", stale_after_seconds=0)
    _release_global_lease(second, "owner-b")


def test_live_owner_is_not_stolen_even_with_zero_stale_budget(lock_root):
    first = _acquire_global_lease(scope="s", owner_token="a", stale_after_seconds=0)
    try:
        with pytest.raises(RunnerError, match="lock could not be acquired"):
            _acquire_global_lease(scope="s", owner_token="b", stale_after_seconds=0)
        _release_global_lease(first, "wrong-owner")
        assert first.descriptor is not None
    finally:
        _release_global_lease(first, "a")


def test_legacy_timestamp_lease_is_not_silently_stolen(lock_root):
    (lock_root / "scope.json").write_text(json.dumps({"pid": os.getpid(), "owner_token": "old"}))
    os.utime(lock_root / "scope.json", (1, 1))
    with pytest.raises(RunnerError) as error:
        _acquire_global_lease(scope="s", owner_token="new", stale_after_seconds=0)
    assert error.value.code == "legacy_scope_lease_present"


@pytest.mark.parametrize("crash", [False, True])
def test_real_process_ownership_and_kernel_release(lock_root, crash):
    lock_path = lock_root / "child.lock"
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {source!r}); "
        "from spec_runner.scope_lock import ScopeLock; "
        "lock=ScopeLock.acquire(Path(sys.argv[1]), 'child'); "
        "print('locked', flush=True); sys.stdin.readline(); lock.release('child')"
    )
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(lock_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # A bounded helper observation; no blind sleep to assume lock acquisition.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(child.stdout.readline)
            try:
                assert ready.result(timeout=10).strip() == "locked"
            except BaseException:
                child.kill()
                child.wait(timeout=10)
                raise
        assert child.poll() is None
        with pytest.raises(RunnerError) as error:
            ScopeLock.acquire(lock_path, "parent")
        assert error.value.code == "global_writer_busy"
        if crash:
            child.kill()
        child.communicate(input="release\n" if not crash else None, timeout=10)
        adopted = ScopeLock.acquire(lock_path, "parent")
        adopted.release("parent")
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
