from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.log_runtime import rotate_launcher_logs


def _logs(root: Path, run_id: str = "run-1") -> tuple[Path, Path]:
    directory = root / "launcher-logs"
    directory.mkdir(parents=True)
    stdout = directory / f"{run_id}.stdout.log"
    stderr = directory / f"{run_id}.stderr.log"
    stdout.write_text("stdout before\n", encoding="utf-8")
    stderr.write_text("stderr before\n", encoding="utf-8")
    return stdout, stderr


def test_rotation_moves_the_pair_creates_fresh_active_logs_and_is_idempotent(tmp_path: Path) -> None:
    stdout, stderr = _logs(tmp_path)

    first = rotate_launcher_logs(control_root=tmp_path, run_id="run-1", rotation_key="rotation-1")

    assert first["state"] == "rotated"
    assert first["replayed"] is False
    rotated = tmp_path / "launcher-logs" / "rotated" / "run-1" / "rotation-1"
    assert (rotated / "stdout.log").read_text(encoding="utf-8") == "stdout before\n"
    assert (rotated / "stderr.log").read_text(encoding="utf-8") == "stderr before\n"
    assert stdout.read_text(encoding="utf-8") == ""
    assert stderr.read_text(encoding="utf-8") == ""

    replay = rotate_launcher_logs(control_root=tmp_path, run_id="run-1", rotation_key="rotation-1")
    assert replay["replayed"] is True
    assert replay["state"] == "rotated"
    receipt = Path(str(first["receipt"]))
    assert json.loads(receipt.read_text(encoding="utf-8"))["state"] == "rotated"


def test_rotation_rolls_back_when_the_second_log_is_locked(tmp_path: Path) -> None:
    stdout, stderr = _logs(tmp_path)
    real_replace = __import__("os").replace

    def replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        if Path(source).name == "run-1.stderr.log":
            raise PermissionError("held")
        real_replace(source, destination)

    with patch("spec_runner.log_runtime.os.replace", side_effect=replace):
        with pytest.raises(RunnerError) as raised:
            rotate_launcher_logs(control_root=tmp_path, run_id="run-1", rotation_key="rotation-1")

    assert raised.value.code == "launcher_log_rotation_failed"
    assert stdout.read_text(encoding="utf-8") == "stdout before\n"
    assert stderr.read_text(encoding="utf-8") == "stderr before\n"
    receipt = tmp_path / "launcher-logs" / "rotation-receipts" / "run-1.rotation-1.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["state"] == "failed"


def test_second_log_failure_does_not_delete_the_unmoved_active_log(tmp_path: Path) -> None:
    stdout, stderr = _logs(tmp_path)
    real_replace = __import__("os").replace

    def replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        if Path(source).name == "run-1.stderr.log":
            raise PermissionError("held")
        real_replace(source, destination)

    with patch("spec_runner.log_runtime.os.replace", side_effect=replace):
        with pytest.raises(RunnerError):
            rotate_launcher_logs(control_root=tmp_path, run_id="run-1", rotation_key="rotation-1")

    assert stderr.is_file()
    assert stderr.read_text(encoding="utf-8") == "stderr before\n"
    assert stdout.is_file()
    assert stdout.read_text(encoding="utf-8") == "stdout before\n"


def test_retention_removes_old_complete_rotation_directories(tmp_path: Path) -> None:
    _logs(tmp_path)
    old = tmp_path / "launcher-logs" / "rotated" / "run-1" / "old"
    old.mkdir(parents=True)
    (old / "stdout.log").write_text("old", encoding="utf-8")
    (old / "stderr.log").write_text("old", encoding="utf-8")
    current = rotate_launcher_logs(control_root=tmp_path, run_id="run-1", rotation_key="current", retain=1)

    assert current["retention"]["outcome"] == "cleaned"
    assert not old.exists()
