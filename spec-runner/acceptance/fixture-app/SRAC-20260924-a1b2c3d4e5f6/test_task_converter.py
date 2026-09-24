import pytest

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
        "counts": {},
    }


def test_preserves_surrounding_whitespace_and_accepts_header_only_input():
    assert convert_csv("id,title,status\n 1 , A title , open \n") == {
        "rows": [{"id": " 1 ", "title": " A title ", "status": " open "}],
        "counts": {},
    }
    assert convert_csv("id,title,status\n") == {"rows": [], "counts": {}}


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
