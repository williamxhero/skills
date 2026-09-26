import json
import shutil
import uuid
from pathlib import Path

import pytest

from task_csv_to_json import ConversionError, convert


@pytest.fixture
def work_dir():
    directory = Path(__file__).with_name(f".test-work-{uuid.uuid4().hex}")
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_convert_writes_compact_utf8_json(work_dir):
    input_path = work_dir / "tasks.csv"
    output_path = work_dir / "tasks.json"
    input_path.write_text(
        'id,title,status\n1,"Fix, parsing",done\n2,整理任务,todo\n',
        encoding="utf-8",
    )

    convert(input_path, output_path)

    expected = {"tasks": [
        {"id": "1", "title": "Fix, parsing", "status": "done"},
        {"id": "2", "title": "整理任务", "status": "todo"},
    ]}
    assert output_path.read_bytes() == (
        json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("", "input CSV is empty"),
        ("id,title,state\n1,Task,todo\n", "header must be exactly"),
        ("id,title,status\n1,Task,blocked\n", "invalid status"),
        ("id,title,status\n1,Task,todo\n1,Other,done\n", "duplicate id"),
        ("id,title,status\n1,Task\n", "exactly 3 fields"),
    ],
)
def test_invalid_csv_is_rejected_without_replacing_output(work_dir, csv_text, message):
    input_path = work_dir / "tasks.csv"
    output_path = work_dir / "tasks.json"
    input_path.write_text(csv_text, encoding="utf-8")
    output_path.write_bytes(b"existing output\n")

    with pytest.raises(ConversionError, match=message):
        convert(input_path, output_path)

    assert output_path.read_bytes() == b"existing output\n"
