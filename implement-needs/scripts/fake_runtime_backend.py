"""Deterministic fake backend scenarios for runtime observability tests."""
from __future__ import annotations

from control_db import ControlDB


SCENARIOS = (
    "normal",
    "external-wait",
    "retry",
    "repair",
    "verification-failure",
)


def _record(db: ControlDB, run_id: str, key: str, entity_id: str, phase: str,
            status: str, duration_ms: float, metadata=None, usage=None):
    return db.record_runtime_observation(
        run_id=run_id,
        observation_key=f"fixture:{run_id}:{key}",
        entity_type="fixture",
        entity_id=entity_id,
        phase=phase,
        status=status,
        scope="run",
        unit="milliseconds",
        duration_ms=duration_ms,
        source="fake-runtime-backend",
        usage=usage,
        metadata={"fixture": True, "scenario_key": key, **(metadata or {})},
    )


def run_scenario(db: ControlDB, run_id: str, scenario: str) -> list[dict]:
    """Emit one stable scenario; repeated calls are idempotent."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    evidence = [f"fixture://runtime/{scenario}"]
    emitted = [
        _record(db, run_id, "controller", "controller-1", "controller_turn", "completed", 5,
                {"role": "controller", "evidence": evidence}),
    ]
    if scenario == "normal":
        emitted.append(_record(db, run_id, "verification", "verification-1", "verification", "passed", 15,
                               {"first_acceptance": True, "evidence": evidence}))
    elif scenario == "external-wait":
        emitted.append(_record(db, run_id, "wait", "wait-1", "external_wait", "completed", 250,
                               {"evidence": evidence}))
        emitted.append(_record(db, run_id, "verification", "verification-1", "verification", "passed", 15,
                               {"first_acceptance": True, "evidence": evidence}))
    elif scenario == "retry":
        emitted.append(_record(db, run_id, "retry", "action-1", "retry", "completed", 20,
                               {"retry": True, "evidence": evidence}))
        emitted.append(_record(db, run_id, "verification", "verification-1", "verification", "passed", 15,
                               {"first_acceptance": False, "evidence": evidence}))
    elif scenario == "repair":
        emitted.append(_record(db, run_id, "repair", "repair-1", "repair", "completed", 30,
                               {"recovery_success": True, "evidence": evidence}))
        emitted.append(_record(db, run_id, "verification", "verification-1", "verification", "passed", 15,
                               {"first_acceptance": False, "evidence": evidence}))
    else:
        emitted.append(_record(db, run_id, "verification", "verification-1", "verification", "failed", 15,
                               {"first_acceptance": False, "evidence": evidence}))
    return emitted
