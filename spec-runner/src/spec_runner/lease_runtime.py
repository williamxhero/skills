"""Process-local health for the durable writer lease.

The SQLite lease is the authority for ownership.  This module only carries a
fast failure signal from the heartbeat thread to an active SDK turn in the
same Runner process; it never grants or renews ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading

from .errors import RunnerError


@dataclass(frozen=True)
class LeaseFailure:
    """Safe, bounded details about why a heartbeat stopped."""

    exception_type: str
    source_code: str | None = None

    def public(self) -> dict[str, object]:
        details: dict[str, object] = {
            "source": "lease_heartbeat",
            "exception_type": self.exception_type,
        }
        if self.source_code:
            details["source_code"] = self.source_code
        return details


class LeaseHealth:
    """Fail-closed health signal shared by one run's heartbeat and workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failure: LeaseFailure | None = None

    def fail(self, error: BaseException) -> None:
        """Remember the first heartbeat failure; later failures add no noise."""
        source_code = error.code if isinstance(error, RunnerError) else None
        failure = LeaseFailure(type(error).__name__, source_code)
        with self._lock:
            if self._failure is None:
                self._failure = failure

    def check(self) -> None:
        """Raise a stable error while this process no longer owns the lease."""
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise RunnerError(
                "writer_lease_lost",
                "the Runner writer lease heartbeat failed; active execution must stop",
                details=failure.public(),
            )


_registry_lock = threading.Lock()
_health_by_run: dict[str, LeaseHealth] = {}


def register(run_id: str) -> LeaseHealth:
    health = LeaseHealth()
    with _registry_lock:
        _health_by_run[run_id] = health
    return health


def unregister(run_id: str, health: LeaseHealth | None = None) -> None:
    with _registry_lock:
        if health is None or _health_by_run.get(run_id) is health:
            del _health_by_run[run_id]


def check(run_id: str) -> None:
    with _registry_lock:
        health = _health_by_run.get(run_id)
    if health is not None:
        health.check()
