"""Published SDK TurnError contract at the adapter result boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex.errors import CodexRpcError, TransportClosedError
from openai_codex.generated.v2_all import TurnError

from spec_runner.codex_adapter import CodexAdapter
from spec_runner.errors import RunnerError


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


def test_sdk_exception_preserves_structured_data_at_the_stream_failure_boundary():
    class Turn:
        id = "turn-exception-1"

        def run(self):
            raise CodexRpcError(
                -32001,
                "server busy",
                {"codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 503}}},
            )

    class Thread:
        id = "thread-exception-1"

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
    with pytest.raises(RunnerError) as raised:
        CodexAdapter(codex_factory=lambda config: Codex(), sdk_module=sdk).run(
            prompt="bounded task", repository_path=Path("C:/repo"), model="model-1", effort="high",
        )

    observed = raised.value.details["fault_observation"]
    assert observed["source"] == "sdk_exception"
    assert observed["error_type"] == "CodexRpcError"
    assert observed["code"] == "responseStreamDisconnected"
    assert observed["http_status"] == 503
    assert observed["family"] == "capacity"
    assert observed["request_admission"] == "accepted"
    assert observed["execution_outcome"] == "unknown"
    assert "sdk_exception_structured_fields" in observed["evidence"]


def test_transport_closed_exception_is_classified_without_a_turn_identity():
    class Codex:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def thread_start(self, **kwargs):
            raise TransportClosedError("Codex client process exited before a turn was admitted")

    sdk = SimpleNamespace(
        Codex=Codex,
        CodexConfig=lambda **kwargs: kwargs,
        Sandbox=SimpleNamespace(workspace_write="workspace-write"),
        ApprovalMode=SimpleNamespace(deny_all="deny_all"),
    )
    with pytest.raises(RunnerError) as raised:
        CodexAdapter(codex_factory=lambda config: Codex(), sdk_module=sdk).run(
            prompt="bounded task", repository_path=Path("C:/repo"), model="model-1", effort="high",
        )

    observed = raised.value.details["fault_observation"]
    assert observed["family"] == "stream_disconnected"
    assert observed["request_admission"] == "unknown"
    assert observed["execution_outcome"] == "unknown"
    assert observed["turn_id"] is None


def test_failed_turn_without_sdk_error_still_exposes_a_receipt_error():
    result = SimpleNamespace(
        id="turn-failed-without-error", status=SimpleNamespace(value="failed"), error=None,
        final_response=None, items=[], started_at=None, completed_at=None,
    )

    class Turn:
        id = "turn-failed-without-error"

        def run(self):
            return result

    class Thread:
        id = "thread-failed-without-error"

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
    assert receipt.status == "failed"
    assert receipt.error == "failed"
    assert receipt.fault_observation is not None
