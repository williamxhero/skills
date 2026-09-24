from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


COMMAND = Path(__file__).with_name("main.py")
SENTINEL = b"keep this output"
FAILURE_ENV = "TASK_CSV_TO_JSON_TEST_FAILURE"


def invoke(tmp_path: Path, *arguments: str, failure: str | None = None):
    environment = os.environ.copy()
    environment.pop(FAILURE_ENV, None)
    if failure is not None:
        environment[FAILURE_ENV] = failure
    return subprocess.run(
        [sys.executable, str(COMMAND), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def assert_rejected(result: subprocess.CompletedProcess[str], output: Path | None = None):
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
    if output is not None:
        assert output.read_bytes() == SENTINEL


def assert_no_temporary_output(parent: Path, output_name: str = "result.json"):
    assert list(parent.glob(f".{output_name}.*.tmp")) == []


def test_valid_csv_emits_compact_ordered_json_and_is_deterministic(tmp_path: Path):
    source = tmp_path / "tasks.csv"
    output = tmp_path / "result.json"
    source.write_text("id,title,status\n2,Second,todo\n1,First,done\n", encoding="utf-8")

    first = invoke(tmp_path, "--input", str(source), "--output", str(output))
    expected = b'{"rows":[{"id":"2","title":"Second","status":"todo"},{"id":"1","title":"First","status":"done"}]}'
    assert first.returncode == 0, first.stderr
    assert first.stdout == ""
    assert first.stderr == ""
    assert output.read_bytes() == expected

    second = invoke(tmp_path, "--input", str(source), "--output", str(output))
    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == expected
    assert_no_temporary_output(tmp_path)


def test_bom_header_only_and_trimmed_values_are_supported(tmp_path: Path):
    source = tmp_path / "tasks.csv"
    output = tmp_path / "result.json"
    source.write_text("\ufeffid,title,status\n 7 , A task , doing \n", encoding="utf-8")
    result = invoke(tmp_path, "--input", str(source), "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "rows": [{"id": "7", "title": "A task", "status": "doing"}]
    }

    source.write_text("id,title,status\n", encoding="utf-8")
    result = invoke(tmp_path, "--input", str(source), "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == b'{"rows":[]}'


@pytest.mark.parametrize(
    "csv_text",
    [
        "title,id,status\na,1,todo\n",
        "id,title,status,extra\n1,a,todo,x\n",
        "id,title\n1,a\n",
        'id,title,status\n1,"unterminated,todo\n',
        "id,title,status\n1,a,todo,extra\n",
        "id,title,status\n\n",
        "id,title,status\n ,a,todo\n",
        "id,title,status\n1, ,todo\n",
        "id,title,status\n1,a, \n",
        "id,title,status\n1,a,todo\n1,b,done\n",
        "id,title,status\n1,a,blocked\n",
    ],
)
def test_invalid_csv_preserves_existing_output(tmp_path: Path, csv_text: str):
    source = tmp_path / "tasks.csv"
    output = tmp_path / "result.json"
    source.write_text(csv_text, encoding="utf-8")
    output.write_bytes(SENTINEL)

    result = invoke(tmp_path, "--input", str(source), "--output", str(output))

    assert_rejected(result, output)
    assert_no_temporary_output(tmp_path)


@pytest.mark.parametrize("arguments", [(), ("--input", "source.csv"), ("--output", "result.json")])
def test_required_paths_are_enforced_by_the_command(tmp_path: Path, arguments: tuple[str, ...]):
    result = invoke(tmp_path, *arguments)
    assert_rejected(result)


def test_path_policy_rejects_missing_non_regular_and_same_file_paths(tmp_path: Path):
    output = tmp_path / "result.json"
    output.write_bytes(SENTINEL)

    missing = invoke(tmp_path, "--input", str(tmp_path / "missing.csv"), "--output", str(output))
    assert_rejected(missing, output)

    directory = tmp_path / "input-directory"
    directory.mkdir()
    non_regular = invoke(tmp_path, "--input", str(directory), "--output", str(output))
    assert_rejected(non_regular, output)

    same_file = invoke(tmp_path, "--input", str(output), "--output", str(output))
    assert_rejected(same_file, output)

    source = tmp_path / "tasks.csv"
    source.write_text("id,title,status\n1,a,todo\n", encoding="utf-8")
    missing_parent = tmp_path / "missing-parent" / "result.json"
    missing_parent_result = invoke(tmp_path, "--input", str(source), "--output", str(missing_parent))
    assert_rejected(missing_parent_result)
    assert not missing_parent.parent.exists()

    output_directory = tmp_path / "output-directory"
    output_directory.mkdir()
    invalid_target = invoke(tmp_path, "--input", str(source), "--output", str(output_directory))
    assert_rejected(invalid_target)
    assert_no_temporary_output(tmp_path)


@pytest.mark.parametrize("failure", ["prepare", "replace"])
def test_injected_publication_failures_preserve_output_and_clean_temporary_files(
    tmp_path: Path, failure: str
):
    source = tmp_path / "tasks.csv"
    output = tmp_path / "result.json"
    source.write_text("id,title,status\n1,a,todo\n", encoding="utf-8")
    output.write_bytes(SENTINEL)

    result = invoke(tmp_path, "--input", str(source), "--output", str(output), failure=failure)

    assert_rejected(result, output)
    assert_no_temporary_output(tmp_path)
