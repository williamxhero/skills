from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.errors import RunnerError
from spec_runner.multi_spec import _run_command


def test_implementation_command_timeout_is_bounded_and_classified(tmp_path: Path):
    with pytest.raises(RunnerError) as error:
        _run_command([sys.executable, "-c", "import time; time.sleep(1)"], cwd=tmp_path, timeout_seconds=1)
    assert error.value.code == "delivery_command_timeout"
    assert error.value.details["timed_out"] is True


def test_invalid_implementation_timeout_is_rejected(tmp_path: Path):
    with pytest.raises(RunnerError) as error:
        _run_command([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=0)
    assert error.value.code == "delivery_timeout_invalid"
