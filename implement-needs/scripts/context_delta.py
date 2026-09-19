"""Versioned, read-only phase-context deltas."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

from context_projection import ContextProjectionError, PHASE_PROJECTIONS
from phase_context import build_snapshot


DELTA_VERSION = "delta-v1"
DELTA_COLLECTIONS = (
    "acceptance",
    "direct_dependencies",
    "decisions",
    "worktree",
    "version",
    "evidence",
    "events",
    "unresolved_exceptions",
)
PHASE_COLLECTIONS = {
    "startup": frozenset({"acceptance", "direct_dependencies", "decisions"}),
    "planning": frozenset({"acceptance", "direct_dependencies", "decisions"}),
    "implementation": frozenset({"acceptance", "direct_dependencies", "decisions", "evidence"}),
    "verification": frozenset({"acceptance", "evidence", "events"}),
    "recovery": frozenset({"direct_dependencies", "evidence", "events", "unresolved_exceptions"}),
    "release": frozenset({"acceptance", "evidence", "events", "unresolved_exceptions"}),
}


class ContextDeltaError(ContextProjectionError):
    """A delta cannot be safely applied to the requested base context."""


def canonical_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _count(value):
    return len(value) if isinstance(value, (list, dict)) else (0 if value in (None, "") else 1)


def _snapshot_value(payload, collection):
    return payload.get(collection, [])


def _descriptor(base_value, current_value, required):
    current_digest = canonical_digest(current_value)
    unchanged = base_value == current_value
    descriptor = {
        "required": required,
        "kind": "unchanged" if unchanged else "changed",
        "digest": current_digest,
        "count": _count(current_value),
    }
    if not unchanged:
        descriptor["value"] = current_value
    return descriptor


def build_delta_context(db, run_id, phase, base_event_cursor, base_state_version=None, base_digests=None):
    if phase not in PHASE_PROJECTIONS:
        raise ContextDeltaError("context_phase_unknown", {"phase": phase})
    if not isinstance(base_event_cursor, int) or isinstance(base_event_cursor, bool) or base_event_cursor < 0:
        raise ContextDeltaError("delta_base_cursor_invalid")
    if base_state_version is not None and (
        not isinstance(base_state_version, int) or isinstance(base_state_version, bool) or base_state_version < 0
    ):
        raise ContextDeltaError("delta_base_version_invalid")
    if base_digests is not None and not isinstance(base_digests, dict):
        raise ContextDeltaError("delta_base_digests_invalid")

    stored = db.read_snapshot(run_id)
    if stored is None:
        raise ContextDeltaError("delta_base_missing", {"run_id": run_id})
    stored_cursor = int(stored["event_cursor"])
    stored_version = int(stored["state_version"])
    if base_event_cursor != stored_cursor:
        raise ContextDeltaError(
            "delta_base_unavailable",
            {"expected_event_cursor": stored_cursor, "actual_event_cursor": base_event_cursor},
        )
    if base_state_version is not None and base_state_version != stored_version:
        raise ContextDeltaError(
            "delta_base_version_mismatch",
            {"expected_state_version": stored_version, "actual_state_version": base_state_version},
        )

    base_payload = stored["payload"]
    current_payload = build_snapshot(db, run_id)
    current_cursor = db.event_cursor(run_id)
    current_exceptions = db.unresolved_exceptions(run_id)
    base_exceptions = []
    values = {collection: _snapshot_value(current_payload, collection) for collection in DELTA_COLLECTIONS}
    values["events"] = db.events_since(run_id, base_event_cursor)
    values["unresolved_exceptions"] = current_exceptions
    base_values = {collection: _snapshot_value(base_payload, collection) for collection in DELTA_COLLECTIONS}
    base_values["events"] = []
    base_values["unresolved_exceptions"] = base_exceptions

    if base_digests:
        for collection, expected in base_digests.items():
            if collection not in DELTA_COLLECTIONS or not isinstance(expected, str):
                raise ContextDeltaError("delta_base_digests_invalid", {"collection": collection})
            actual = canonical_digest(base_values[collection])
            if actual != expected:
                raise ContextDeltaError(
                    "delta_base_digest_mismatch",
                    {"collection": collection, "expected": expected, "actual": actual},
                )

    required = PHASE_COLLECTIONS[phase]
    collections = {}
    for collection in DELTA_COLLECTIONS:
        if base_values[collection] == values[collection]:
            continue
        collections[collection] = _descriptor(
            base_values[collection],
            values[collection],
            collection in required,
        )
    base_digest = canonical_digest(base_values)
    return {
        "context_version": DELTA_VERSION,
        "mode": "delta",
        "run_id": run_id,
        "phase": phase,
        "business_version": db.business_version(run_id),
        "event_cursor": current_cursor,
        "base": {
            "pointer": f"snapshot://run/{quote(run_id, safe='')}",
            "event_cursor": base_event_cursor,
            "digest": base_digest,
        },
        "collections": collections,
    }
