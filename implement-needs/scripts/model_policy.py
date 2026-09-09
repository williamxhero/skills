"""Load the single Implement Needs model and effort allow-list."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[1] / "references" / "model-policy.json"


def load_policy() -> tuple[dict[str, Any] | None, str | None]:
    """Return a usable policy, or a deterministic reason suitable for a gate."""
    try:
        value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{exc.__class__.__name__}"
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "owner",
        "models",
        "efforts",
        "model_rank",
        "effort_rank",
    }:
        return None, "invalid_shape"
    models = value.get("models")
    efforts = value.get("efforts")
    model_rank = value.get("model_rank")
    effort_rank = value.get("effort_rank")
    if (
        value.get("schema_version") != 1
        or value.get("owner") != "implement-needs"
        or not isinstance(models, list)
        or not isinstance(efforts, list)
        or not isinstance(model_rank, dict)
        or not isinstance(effort_rank, dict)
        or not models
        or not efforts
        or any(not isinstance(item, str) or not item for item in models + efforts)
        or len(set(models)) != len(models)
        or len(set(efforts)) != len(efforts)
        or set(model_rank) != set(models)
        or set(effort_rank) != set(efforts)
        or any(
            isinstance(rank, bool) or not isinstance(rank, int)
            for rank in model_rank.values()
        )
        or any(
            isinstance(rank, bool) or not isinstance(rank, int)
            for rank in effort_rank.values()
        )
        or sorted(model_rank.values()) != list(range(len(models)))
        or sorted(effort_rank.values()) != list(range(len(efforts)))
    ):
        return None, "invalid_values"
    return value, None
