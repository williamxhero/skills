"""Build deterministic, read-only metrics from runtime observations."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict


METRICS_SCHEMA_VERSION = 1
DEFAULT_BASELINE_VERSION = "runtime-observations-v1"
USAGE_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "cost_usd",
)


def _percentile(values: list[float], percentile: float):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rate(numerator: int, denominator: int):
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "unit": "ratio",
        "unknown": denominator == 0,
    }


def _usage(rows):
    result = {}
    for field in USAGE_FIELDS:
        values = []
        unknown = False
        for row in rows:
            usage = json.loads(row["usage"] or "{}")
            if field not in usage:
                unknown = True
                continue
            if not _number(usage[field]):
                unknown = True
                continue
            values.append(usage[field])
        result[field] = {
            "value": sum(values) if values and not unknown else None,
            "unit": "count" if field.endswith("tokens") else "usd",
            "sample_count": len(values),
            "unknown": unknown or not values,
        }
    return result


def build_metrics(db, run_id: str, baseline_version: str = DEFAULT_BASELINE_VERSION) -> dict:
    """Return a stable metrics projection without changing the database."""
    rows = db.conn.execute(
        "SELECT * FROM runtime_observations WHERE run_id=? ORDER BY observation_id",
        (run_id,),
    ).fetchall()
    durations = [float(row["duration_ms"]) for row in rows if row["duration_ms"] is not None]
    phase_duration = defaultdict(float)
    phase_counts = Counter()
    role_turns = Counter()
    retry_count = 0
    duplicate_count = 0
    first_acceptance = []
    recovery_success = []
    evidence = []
    for row in rows:
        phase = row["phase"]
        phase_counts[phase] += 1
        if row["duration_ms"] is not None:
            phase_duration[phase] += float(row["duration_ms"])
        metadata = json.loads(row["metadata"] or "{}")
        role = metadata.get("role")
        if phase in {"controller_turn", "worker_turn"}:
            role_turns[role or phase.removesuffix("_turn")] += 1
        if phase == "retry" or metadata.get("retry") is True:
            retry_count += 1
        if metadata.get("duplicate_external_operation") is True:
            duplicate_count += 1
        if isinstance(metadata.get("first_acceptance"), bool):
            first_acceptance.append(metadata["first_acceptance"])
        if isinstance(metadata.get("recovery_success"), bool):
            recovery_success.append(metadata["recovery_success"])
        if isinstance(metadata.get("evidence"), list):
            evidence.extend(item for item in metadata["evidence"] if isinstance(item, str))
    durations = [float(value) for value in durations]
    phase_duration_result = {
        phase: {"value": value, "unit": "milliseconds", "sample_count": phase_counts[phase]}
        for phase, value in sorted(phase_duration.items())
    }
    phase_counts_result = {phase: phase_counts[phase] for phase in sorted(phase_counts)}
    total_duration = sum(durations) if durations else None
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "run_id": run_id,
        "baseline_version": baseline_version,
        "source": {
            "observation_count": len(rows),
            "observation_ids": [row["observation_id"] for row in rows],
            "evidence": sorted(set(evidence)),
        },
        "metrics": {
            "total_duration_ms": {
                "value": total_duration, "unit": "milliseconds",
                "sample_count": len(durations), "unknown": not durations,
            },
            "phase_duration_ms": phase_duration_result,
            "phase_counts": phase_counts_result,
            "controller_turns": role_turns.get("controller", 0),
            "worker_turns": role_turns.get("worker", 0),
            "total_turns": role_turns.get("controller", 0) + role_turns.get("worker", 0),
            "waiting_duration_ms": phase_duration_result.get(
                "external_wait", {"value": 0, "unit": "milliseconds", "sample_count": 0}
            ),
            "retry_count": retry_count,
            "duplicate_external_operations": duplicate_count,
            "first_acceptance_rate": _rate(sum(first_acceptance), len(first_acceptance)),
            "recovery_success_rate": _rate(sum(recovery_success), len(recovery_success)),
            "duration_percentiles_ms": {
                "p50": _percentile(durations, 50),
                "p95": _percentile(durations, 95),
                "sample_count": len(durations),
                "unknown": not durations,
            },
            "usage": _usage(rows),
        },
        "definitions": {
            "total_duration_ms": "sum of observed durations; not wall-clock time",
            "first_acceptance_rate": "first_acceptance=true observations divided by boolean first_acceptance observations",
            "recovery_success_rate": "recovery_success=true observations divided by boolean recovery_success observations",
            "duration_percentiles_ms": "nearest-rank percentiles over observed durations",
            "unknown": "the source did not provide a numeric value or denominator",
        },
    }
