"""Published SDK TurnError contract at the adapter result boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex.generated.v2_all import TurnError

from spec_runner.codex_adapter import CodexAdapter


@pytest.mark.parametrize(
    ("info", "expected_code", "expected_status", "expected_family"),
    [
        ({"responseStreamDisconnected": {"httpStatusCode": 503}}, "responseStreamDisconnected", 503, "capacity"),
        ({"responseStreamConnectionFailed": {}}, "responseStreamConnectionFailed", None, "stream_disconnected"),
        ("unauthorized", "unauthorized", None, "authorization"),
        (None, None, None, "unknown"),
    ],
)
def test_failed_turn_preserves_sdk_error_info_without_persisting_secret(info, expected_code, expected_status, expected_family):
    payload = {"message": "provider failed token=private-value"}
    if info is not None:
        payload["codexErrorInfo"] = info
    turn_error = TurnError.model_validate(payload)
    result = SimpleNamespace(
        id="turn-1", status=SimpleNamespace(value="failed"), error=turn_error,
        final_response=None, items=[], started_at=None, completed_at=None,
    )

    class Turn:
        id = "turn-1"

        def run(self):
            return result

    class Thread:
        id = "thread-1"

        def turn(self, prompt, **kwargs):
            return Turn()

    class Codex:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def thread_start(self, **kwargs):
            return Thread()

    sdk = SimpleNamespace(
        Codex=Codex,
        CodexConfig=lambda **kwargs: kwargs,
        Sandbox=SimpleNamespace(workspace_write="workspace-write"),
        ApprovalMode=SimpleNamespace(deny_all="deny_all"),
    )
    receipt = CodexAdapter(codex_factory=lambda config: Codex(), sdk_module=sdk).run(
        prompt="bounded task", repository_path=Path("C:/repo"), model="model-1", effort="high",
    )
    observed = receipt.fault_observation
    assert observed is not None
    assert observed["code"] == expected_code
    assert observed["http_status"] == expected_status
    assert observed["family"] == expected_family
    assert observed["source"] == "sdk_result"
    assert observed["confidence"] == ("structured" if info is not None else "fallback_text")
    assert observed["request_admission"] == "accepted"
    assert observed["execution_outcome"] == "failed"
    assert "private-value" not in str(receipt.public())
