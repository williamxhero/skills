"""Durable business-question validation for the public Runner lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import RunnerError


def pending_question(*, control_root: Path, run: object, question_id: str) -> dict[str, object]:
    """Read the durable worker question before accepting a public answer."""
    artifact_root = Path(str(getattr(run, "artifact_root", "artifacts")))
    artifact = artifact_root if artifact_root.is_absolute() else control_root / artifact_root
    run_id = str(getattr(run, "run_id"))
    path = artifact / run_id / "worker-result.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError(
            "answer_questions_missing",
            "the waiting run has no readable durable question receipt",
            details={"run_id": run_id},
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != "spec-runner-worker-result/v1":
        raise RunnerError("answer_questions_invalid", "the durable question receipt has an unsupported schema")
    run_digest = str(getattr(run, "input_digest", ""))
    if not run_digest or document.get("input_digest") != run_digest:
        raise RunnerError(
            "answer_input_stale",
            "the durable question receipt belongs to a different requirement revision",
            details={"run_id": run_id},
        )
    questions = document.get("questions")
    if not isinstance(questions, list):
        raise RunnerError("answer_questions_invalid", "the durable question receipt has no question list")
    matches = [
        question for question in questions
        if isinstance(question, dict) and question.get("id") == question_id
    ]
    if len(matches) != 1:
        raise RunnerError(
            "answer_question_unknown",
            "the question is not pending for this run",
            details={"run_id": run_id, "question_id": question_id},
        )
    return matches[0]
