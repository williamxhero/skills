from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

from spec_runner.delivery import cleanup_managed_workspace, prepare_workspace


_DETACHED_LOCK_HOLDER = r"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

path, ready, release = map(Path, sys.argv[1:])
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
create_file = kernel32.CreateFileW
create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
create_file.restype = wintypes.HANDLE
handle = create_file(str(path), 0x80000000, 0x00000001 | 0x00000002, None, 3, 0, None)
if handle == wintypes.HANDLE(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.01)
if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
    raise ctypes.WinError(ctypes.get_last_error())
"""


def _deny_delete_share(path: Path, *, directory: bool) -> int:
    """Open a real Win32 handle that permits reads/writes but denies deletion."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE (not DELETE)
        None,
        3,  # OPEN_EXISTING
        0x02000000 if directory else 0,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


@unittest.skipUnless(os.name == "nt", "requires Windows native share semantics")
class WindowsCleanupLockTests(unittest.TestCase):
    def _workspace(self, root: Path, *, key: str) -> tuple[Path, Path, Path, Path]:
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "runner@example.invalid"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Spec Runner"], cwd=repository, check=True)
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
        workspace_root = root / "managed-workspaces"
        prepared = prepare_workspace(
            repository=repository,
            workspace_root=workspace_root,
            run_id=f"00000000-0000-0000-0000-0000000000{key}",
            spec_key="SR-Windows",
            base_ref="refs/heads/main",
        )
        return repository, workspace_root, Path(prepared["workspace"]), Path(prepared["manifest"])

    def test_db_log_and_directory_locks_become_pending_then_retry_cleanly(self) -> None:
        for key, path_name, directory in (
            ("01", "artifact.sqlite3", False),
            ("02", "runner.log", False),
            ("03", "artifacts", True),
        ):
            with self.subTest(resource=path_name), tempfile.TemporaryDirectory(prefix="spec runner 中文 ") as temp:
                repository, workspace_root, workspace, manifest = self._workspace(Path(temp), key=key)
                locked = workspace / path_name
                if directory:
                    locked.mkdir()
                    (locked / "marker.txt").write_text("locked\n", encoding="utf-8")
                else:
                    locked.write_text("locked\n", encoding="utf-8")
                subprocess.run(["git", "add", "--all"], cwd=workspace, check=True)
                subprocess.run(["git", "commit", "-qm", f"add {path_name}"], cwd=workspace, check=True)
                handle = _deny_delete_share(locked, directory=directory)
                try:
                    pending = cleanup_managed_workspace(
                        repository=repository,
                        workspace_root=workspace_root,
                        workspace=workspace,
                        manifest=manifest,
                    )
                    self.assertEqual(pending["outcome"], "pending")
                    self.assertTrue(workspace.exists())
                    self.assertTrue(manifest.exists())
                finally:
                    _close_handle(handle)
                cleaned = cleanup_managed_workspace(
                    repository=repository,
                    workspace_root=workspace_root,
                    workspace=workspace,
                    manifest=manifest,
                )
                self.assertEqual(cleaned["outcome"], "cleaned", cleaned)
                self.assertFalse(workspace.exists())
                self.assertFalse(manifest.exists())

    def test_detached_process_lock_retries_after_exact_holder_exits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec runner 中文 ") as temp:
            repository, workspace_root, workspace, manifest = self._workspace(Path(temp), key="04")
            locked = workspace / "artifact.sqlite3"
            locked.write_text("locked\n", encoding="utf-8")
            subprocess.run(["git", "add", "artifact.sqlite3"], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-qm", "add locked database"], cwd=workspace, check=True)
            ready = workspace_root / "holder-ready"
            release = workspace_root / "holder-release"
            holder = subprocess.Popen(
                [sys.executable, "-c", _DETACHED_LOCK_HOLDER, str(locked), str(ready), str(release)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(holder.poll(), None, "the exact lock holder exited before signaling readiness")
                self.assertTrue(ready.exists(), "the exact lock holder did not become ready")
                pending = cleanup_managed_workspace(
                    repository=repository,
                    workspace_root=workspace_root,
                    workspace=workspace,
                    manifest=manifest,
                )
                self.assertEqual(pending["outcome"], "pending")
                self.assertTrue(workspace.exists())
            finally:
                release.touch()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.terminate()
                    holder.wait(timeout=5)
            self.assertEqual(holder.returncode, 0)
            cleaned = cleanup_managed_workspace(
                repository=repository,
                workspace_root=workspace_root,
                workspace=workspace,
                manifest=manifest,
            )
            self.assertEqual(cleaned["outcome"], "cleaned", cleaned)
