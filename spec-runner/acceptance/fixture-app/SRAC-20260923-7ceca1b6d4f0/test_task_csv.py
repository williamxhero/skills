from __future__ import annotations

import pytest

from task_csv import CSVValidationError, parse_tasks_csv


def test_accepts_required_headers_in_any_order_and_preserves_row_order() -> None:
    csv_text = "status,id,title\nopen,1,First\nclosed,2,Second\n"

    assert parse_tasks_csv(csv_text) == [
        {"id": "1", "title": "First", "status": "open"},
        {"id": "2", "title": "Second", "status": "closed"},
    ]


def test_ignores_extra_columns() -> None:
    assert parse_tasks_csv(
        "id,owner,title,status,priority\n1,Alice,Task,open,high\n"
    ) == [{"id": "1", "title": "Task", "status": "open"}]


def test_parses_quoted_fields_and_preserves_their_value() -> None:
    assert parse_tasks_csv(
        'id,title,status\n1,"Exact, ""quoted"" title",open\n'
    ) == [{"id": "1", "title": 'Exact, "quoted" title', "status": "open"}]


@pytest.mark.parametrize(
    "csv_text",
    [
        'id,title,status\n1,"unterminated,open\n',
        "id,title,status\n1,title\n",
        "id,title,status\n1,title,open,extra\n",
    ],
)
def test_rejects_malformed_csv_records(csv_text: str) -> None:
    with pytest.raises(CSVValidationError):
        parse_tasks_csv(csv_text)


@pytest.mark.parametrize("field", ["id", "title", "status"])
@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_rejects_blank_or_whitespace_only_required_values(
    field: str, blank: str
) -> None:
    values = {"id": "1", "title": "Task", "status": "open"}
    values[field] = blank
    csv_text = (
        "id,title,status\n"
        f'{values["id"]},{values["title"]},{values["status"]}\n'
    )

    with pytest.raises(CSVValidationError):
        parse_tasks_csv(csv_text)


def test_preserves_nonblank_values_exactly() -> None:
    assert parse_tasks_csv(
        'id,title,status\n"  ID-1  ","  Keep spaces  "," open "\n'
    ) == [{"id": "  ID-1  ", "title": "  Keep spaces  ", "status": " open "}]


def test_header_only_input_is_an_empty_dataset() -> None:
    assert parse_tasks_csv("title,status,id\n") == []


def test_rejects_missing_required_headers() -> None:
    with pytest.raises(CSVValidationError):
        parse_tasks_csv("id,title,owner\n1,Task,Alice\n")
