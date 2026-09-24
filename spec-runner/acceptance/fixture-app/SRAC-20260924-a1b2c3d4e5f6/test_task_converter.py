import pytest

import task_converter
from task_converter import ValidationError, convert_csv


def test_normalizes_reordered_headers_and_ignores_extra_columns():
    result = convert_csv(
        "status,metadata,id,title\n"
        "open,owned,task-2,Second\n"
        "done,archived,task-1,First\n"
    )

    assert list(result) == ["rows", "counts"]
    assert result == {
        "rows": [
            {"id": "task-2", "title": "Second", "status": "open"},
            {"id": "task-1", "title": "First", "status": "done"},
        ],
        "counts": {"done": 1, "open": 1},
    }


def test_preserves_surrounding_whitespace_and_accepts_header_only_input():
    assert convert_csv("id,title,status\n 1 , A title , open \n") == {
        "rows": [{"id": " 1 ", "title": " A title ", "status": " open "}],
        "counts": {" open ": 1},
    }
    assert convert_csv("id,title,status\n") == {"rows": [], "counts": {}}


def test_counts_exact_statuses_in_sorted_order_without_changing_rows():
    result = convert_csv(
        "id,title,status\n"
        "1,First,éxito\n"
        "2,Second,open\n"
        "3,Third,Open\n"
        "4,Fourth,éxito\n"
    )

    assert list(result) == ["rows", "counts"]
    assert result["rows"] == [
        {"id": "1", "title": "First", "status": "éxito"},
        {"id": "2", "title": "Second", "status": "open"},
        {"id": "3", "title": "Third", "status": "Open"},
        {"id": "4", "title": "Fourth", "status": "éxito"},
    ]
    counts = result["counts"]
    assert list(counts) == ["Open", "open", "éxito"]
    assert counts == {"Open": 1, "open": 1, "éxito": 2}


@pytest.mark.parametrize(
    "csv_text, message",
    [
        ("ID,title,status\n1,Title,open\n", "missing required header"),
        ("id,id,title,status\n1,1,Title,open\n", "duplicate header"),
        ("id,title,status\n1,Title\n", "blank required value"),
        ("id,title,status\n\n", "blank row"),
        ("id,title,status\n1,Title,open\n1,Other,done\n", "duplicate id"),
        ('id,title,status\n1,"unterminated,open\n', "malformed CSV"),
    ],
)
def test_rejects_invalid_input(csv_text, message):
    with pytest.raises(ValidationError, match=message):
        convert_csv(csv_text)


def test_cli_usage_error_is_one_concise_diagnostic(capsys):
    assert task_converter.main([]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert captured.err.count("\n") == 1
