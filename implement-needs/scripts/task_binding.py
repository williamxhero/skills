"""Bind a discovered task backend record to a registered attempt.

Title tokens only discover candidates. Binding requires a formal readback to agree
with the registry on the full identity tuple. A legacy row with no ``task_id`` is
excluded from identity gating but stays in the registry for audit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

UNTRACKED = "legacy_untracked"
BOUND = "bound"
AMBIGUOUS = "identity_ambiguous"
MISMATCH = "identity_mismatch"
UNRESOLVED = "identity_unresolved"

#: Fields that a formal thread readback must agree with before rebinding.
REQUIRED_MATCH_FIELDS = (
    "formal_thread_id", "host_id", "task_id", "run_id", "attempt_id",
    "owner_id", "cwd", "project_id",
)


@dataclass(frozen=True)
class Candidate:
    """A formal backend record proposed as the recovery target."""

    formal_thread_id: str | None
    host_id: str | None
    title: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    owner_id: str | None = None
    cwd: str | None = None
    project_id: str | None = None
    lifecycle: str | None = None
    readback_evidence: tuple[str, ...] = ()

    @property
    def identified(self) -> bool:
        """True when the backend readback carries the full identity tuple."""
        return all(getattr(self, name) for name in REQUIRED_MATCH_FIELDS)

    @property
    def observed(self) -> bool:
        """True when identity is accompanied by lifecycle and retained evidence."""
        return bool(self.identified and self.lifecycle and self.readback_evidence)


@dataclass(frozen=True)
class BindingDecision:
    status: str
    reason: str
    mismatched_fields: tuple[str, ...] = ()
    candidate: Candidate | None = None

    @property
    def may_bind(self) -> bool:
        return self.status == BOUND

    @property
    def blocks_creation(self) -> bool:
        """Ambiguity and unrecovered registrations forbid creating a replacement."""
        return self.status in {AMBIGUOUS, MISMATCH, UNRESOLVED}


def encode_readback(value: object) -> str:
    """Serialize readback evidence for storage in a text column."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def decode_readback(value: str | None) -> object:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _registry_fields(registered: dict) -> dict[str, str | None]:
    return {
        "formal_thread_id": registered.get("formal_thread_id"),
        "host_id": registered.get("host_id"),
        "task_id": registered.get("task_id"),
        "run_id": registered.get("run_id"),
        "attempt_id": registered.get("attempt_id"),
        # ``owner_id`` is the v2 column; ``owner`` keeps old exports bindable.
        "owner_id": registered.get("owner_id") or registered.get("owner"),
        "cwd": registered.get("cwd"),
        "project_id": registered.get("project_id"),
    }


def bind_candidate(registered: dict, candidate: Candidate) -> BindingDecision:
    """Decide whether one backend candidate is the registered attempt.

    Fails closed: an unidentifiable candidate, a partial backend readback, or any
    field disagreement between the registry and the formal readback blocks
    binding, so the controller reconciles instead of creating a replacement.
    """
    if not registered.get("task_id"):
        return BindingDecision(UNTRACKED, "registry row carries no task identity", candidate=candidate)
    if not candidate.identified:
        missing = tuple(
            name for name in REQUIRED_MATCH_FIELDS if not getattr(candidate, name)
        )
        return BindingDecision(
            UNRESOLVED,
            "formal readback is missing identity fields",
            mismatched_fields=missing,
            candidate=candidate,
        )
    mismatched = tuple(
        name
        for name, expected in _registry_fields(registered).items()
        if expected is not None and getattr(candidate, name) != expected
    )
    if mismatched:
        return BindingDecision(
            MISMATCH,
            "formal readback disagrees with the registry",
            mismatched_fields=mismatched,
            candidate=candidate,
        )
    if not candidate.lifecycle:
        return BindingDecision(
            UNRESOLVED,
            "binding requires a formal lifecycle readback",
            mismatched_fields=("lifecycle",),
            candidate=candidate,
        )
    if not candidate.readback_evidence:
        return BindingDecision(
            UNRESOLVED,
            "binding requires retained formal readback evidence",
            candidate=candidate,
        )
    return BindingDecision(BOUND, "formal readback matches the registry", candidate=candidate)


def bind_candidates(registered: dict, candidates: list[Candidate]) -> BindingDecision:
    """Bind only when exactly one candidate matches the registered attempt."""
    if not candidates:
        return BindingDecision(UNRESOLVED, "no candidate found in the task backend")
    if len(candidates) > 1:
        return BindingDecision(AMBIGUOUS, f"{len(candidates)} candidates matched the recovery index")
    return bind_candidate(registered, candidates[0])


def may_advance_attempt(lifecycle: str, outcome: str) -> bool:
    """Advance only after archive readback or the narrow absence tombstone."""
    return lifecycle == "archived" or (
        lifecycle == "tombstoned" and outcome == "backend_absent_after_create"
    )
