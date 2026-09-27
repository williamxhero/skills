"""Shared validation for source-thread handover evidence."""

from __future__ import annotations

def valid_handover_evidence(thread_id: str, evidence: object) -> bool:
    """Require proof that the source stopped before ownership can move."""
    if not isinstance(evidence, dict):
        return False
    limits = evidence.get("evidence_limits")
    readback = evidence.get("readback")
    return (
        evidence.get("schema_version") == "spec-runner-sdk-thread-interrupt/v1"
        and evidence.get("accepted") is True
        and evidence.get("thread_id") == thread_id
        and evidence.get("source_writer_state") == "stopped"
        and evidence.get("dispatcher_state") == "quiesced"
        and isinstance(limits, dict)
        and limits.get("source_stop_confirmed") is True
        and limits.get("dispatcher_quiesced") is True
        and limits.get("ownership_transferred") is True
        and isinstance(readback, dict)
        and readback.get("source_thread_id") == thread_id
        and readback.get("observed_status") in {"completed", "idle", "archived"}
    )
