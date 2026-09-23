"""Process-held writer exclusion. Timestamps are diagnostic, not ownership.

The inode must remain stable: never unlink a released lock file, since another
process may already have it open. The kernel releases the lock on process exit.
"""
from __future__ import annotations

import errno
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import RunnerError


@dataclass
class ScopeLock:
    path: Path
    owner_token: str
    descriptor: int | None

    @classmethod
    def acquire(cls, path: Path, owner_token: str) -> "ScopeLock":
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.set_inheritable(descriptor, False)
        try:
            if os.name == "nt":
                import msvcrt
                # Windows can lock a byte past EOF; do not write before owning it.
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            code = "global_writer_busy" if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} else "scope_lock_unavailable"
            raise RunnerError(code, "repository writer lock could not be acquired", details={"path": str(path)}) from exc
        try:
            metadata = json.dumps({"owner_token": owner_token, "pid": os.getpid()}).encode("utf-8")
            os.write(descriptor, b"\0" + metadata)
            os.ftruncate(descriptor, len(metadata) + 1)
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return cls(path, owner_token, descriptor)

    def release(self, owner_token: str) -> None:
        if self.descriptor is None or owner_token != self.owner_token:
            return
        descriptor, self.descriptor = self.descriptor, None
        # Closing the only non-inherited handle releases the kernel lock.
        os.close(descriptor)
