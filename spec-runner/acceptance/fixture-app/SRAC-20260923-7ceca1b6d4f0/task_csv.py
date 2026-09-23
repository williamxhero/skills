"""Parse and validate task CSV input.

This module deliberately stops at the input boundary.  It does not serialize
records or perform file/output orchestration; callers receive parsed records
in the same order as their CSV rows.
"""

from __future__ import annotations

import csv
from io import StringIO
from typing import TypedDict


REQUIRED_HEADERS = ("id", "title", "status")


class TaskRecord(TypedDict):
    id: str
    title: str
    status: str


class CSVValidationError(ValueError):
    """Raised when task CSV input cannot be parsed or fails validation."""


def parse_tasks_csv(csv_text: str) -> list[TaskRecord]:
    """Parse task records from CSV text.

    Required headers may appear in any order and additional columns are
    ignored.  Field values are returned exactly as decoded by the CSV parser;
    in particular, validation never strips or otherwise normalizes values.
    """

    if not isinstance(csv_text, str):
        raise TypeError("csv_text must be a string")

    reader = csv.reader(StringIO(csv_text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CSVValidationError("CSV input must include a header row") from exc
    except csv.Error as exc:
        raise CSVValidationError(f"malformed CSV: {exc}") from exc

    positions = _header_positions(header)
    records: list[TaskRecord] = []
    row_number = 2
    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise CSVValidationError(
                f"malformed CSV on row {row_number}: {exc}"
            ) from exc

        if len(row) != len(header):
            raise CSVValidationError(
                f"row {row_number} has {len(row)} fields; expected {len(header)}"
            )
        values = {field: row[positions[field]] for field in REQUIRED_HEADERS}

        for field, value in values.items():
            if not value.strip():
                raise CSVValidationError(
                    f"row {row_number} has a blank {field} value"
                )
        records.append(values)
        row_number += 1

    return records


def _header_positions(header: list[str]) -> dict[str, int]:
    missing = [field for field in REQUIRED_HEADERS if field not in header]
    if missing:
        raise CSVValidationError(
            "missing required header(s): " + ", ".join(missing)
        )

    duplicates = [
        field for field in REQUIRED_HEADERS if header.count(field) > 1
    ]
    if duplicates:
        raise CSVValidationError(
            "duplicate required header(s): " + ", ".join(duplicates)
        )

    return {field: header.index(field) for field in REQUIRED_HEADERS}


# A short alias keeps the parser seam convenient for callers without adding a
# second implementation or any command/output behavior.
parse_csv = parse_tasks_csv
