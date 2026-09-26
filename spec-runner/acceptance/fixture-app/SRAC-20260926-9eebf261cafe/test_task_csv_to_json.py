import subprocess
import shutil
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).parent


def run_converter(input_path: Path, output_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "task_csv_to_json", str(input_path), str(output_path)],
        cwd=ROOT,
        capture_output=True,
    )


def make_work_dir() -> Path:
    directory = ROOT / f".test-work-{uuid.uuid4().hex}"
    directory.mkdir()
    return directory


def remove_work_dir(directory: Path) -> None:
    shutil.rmtree(directory)


def test_process_boundary_converts_and_normalizes_valid_csv() -> None:
    work_dir = make_work_dir()
    try:
        source = work_dir / "tasks.csv"
        output = work_dir / "tasks.json"
        source.write_text(
            'id,title,status\n 1 ,"Build, API" , todo\n二,"Say ""hello""",done\n',
            encoding="utf-8",
        )
        output.write_bytes(b"old result that must be replaced\n")

        first = run_converter(source, output)
        assert first.returncode == 0
        first_bytes = output.read_bytes()
        second = run_converter(source, output)

        assert second.returncode == 0
        assert first.stdout == second.stdout == b""
        assert first.stderr == second.stderr == b""
        assert output.read_bytes() == first_bytes
        assert first_bytes == (
            '{"tasks":[{"id":"1","title":"Build, API","status":"todo"},'
            '{"id":"二","title":"Say \\"hello\\"","status":"done"}]}\n'
        ).encode("utf-8")
    finally:
        remove_work_dir(work_dir)


def test_validation_failures_are_safe_at_process_boundary() -> None:
    work_dir = make_work_dir()
    try:
        output = work_dir / "tasks.json"
        original = b"known-good\n"
        output.write_bytes(original)

        invalid_inputs = {
            "empty.csv": b"",
            "blank.csv": b"id,title,status\n\n",
            "header.csv": b"id,status,title\n1,todo,Task\n",
            "missing-header.csv": b"id,title\n1,Task\n",
            "extra-header.csv": b"id,title,status,extra\n1,Task,todo,x\n",
            "duplicate-header.csv": b"id,title,id\n1,Task,1\n",
            "empty-id.csv": b"id,title,status\n ,Task,todo\n",
            "empty-title.csv": b"id,title,status\n1, ,todo\n",
            "status.csv": b"id,title,status\n1,Task,blocked\n",
            "duplicate.csv": b"id,title,status\n1,Task,todo\n 1 ,Other,done\n",
            "short-row.csv": b"id,title,status\n1,Task\n",
            "long-row.csv": b"id,title,status\n1,Task,todo,extra\n",
            "malformed.csv": b'id,title,status\n1,"unterminated,todo\n',
            "quote.csv": b'id,title,status\n1,ab"cd,todo\n',
            "trailing-quote.csv": b'id,title,status\n1,"Task"x,todo\n',
            "encoding.csv": b"id,title,status\n1,Task,todo\n\xff",
        }

        for filename, content in invalid_inputs.items():
            source = work_dir / filename
            source.write_bytes(content)
            result = run_converter(source, output)
            assert result.returncode != 0, filename
            assert result.stdout == b"", filename
            assert result.stderr.startswith(b"error: "), filename
            assert output.read_bytes() == original, filename
    finally:
        remove_work_dir(work_dir)


def test_missing_output_directory_is_not_created() -> None:
    work_dir = make_work_dir()
    try:
        source = work_dir / "tasks.csv"
        missing_directory = work_dir / "missing"
        source.write_text("id,title,status\n1,Task,todo\n", encoding="utf-8")

        result = run_converter(source, missing_directory / "tasks.json")

        assert result.returncode != 0
        assert result.stdout == b""
        assert b"output directory does not exist" in result.stderr
        assert not missing_directory.exists()
    finally:
        remove_work_dir(work_dir)


def test_delivery_failure_preserves_directory_and_cleans_temporary_files() -> None:
    work_dir = make_work_dir()
    try:
        source = work_dir / "tasks.csv"
        output = work_dir / "tasks.json"
        source.write_text("id,title,status\n1,Task,todo\n", encoding="utf-8")
        output.mkdir()

        result = run_converter(source, output)

        assert result.returncode != 0
        assert result.stdout == b""
        assert result.stderr.startswith(b"error: ")
        assert output.is_dir()
        assert not list(work_dir.glob(".tasks.json.*.tmp"))
    finally:
        remove_work_dir(work_dir)
