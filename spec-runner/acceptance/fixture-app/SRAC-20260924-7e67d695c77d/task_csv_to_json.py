"""Parse and validate task CSV data into a stable JSON-compatible result.

The parser is deliberately independent of filesystem output.  Callers can use
``parse_csv`` with text or ``parse_file`` with a temporary input file and only
write a destination after validation has completed successfully.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TextIO as TextIOType


REQUIRED_COLUMNS = ("id", "title", "status")


class ValidationError(ValueError):
    """An input CSV does not satisfy the task data contract."""


def _validate_header(header: list[str]) -> dict[str, int]:
    duplicate_required = [
        name for name in REQUIRED_COLUMNS if header.count(name) > 1
    ]
    if duplicate_required:
        joined = ", ".join(duplicate_required)
        raise ValidationError(f"duplicate required column(s): {joined}")

    positions = {name: index for index, name in enumerate(header)}
    missing = [name for name in REQUIRED_COLUMNS if name not in positions]
    if missing:
        joined = ", ".join(missing)
        raise ValidationError(f"missing required column(s): {joined}")
    return positions


def _parse_stream(stream: TextIOType[str]) -> dict[str, object]:
    reader = csv.reader(stream, strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValidationError(
            "input CSV is empty; expected a header containing id, title, and status"
        ) from exc
    except csv.Error as exc:
        raise ValidationError(f"malformed CSV header: {exc}") from exc

    positions = _validate_header(header)
    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    try:
        for row in reader:
            line_number = reader.line_num
            if not row:
                raise ValidationError(f"row {line_number}: blank rows are not allowed")

            values = {
                name: row[index] if index < len(row) else ""
                for name, index in positions.items()
            }
            for name in REQUIRED_COLUMNS:
                if not values[name].strip():
                    raise ValidationError(
                        f"row {line_number}: required value '{name}' must not be blank"
                    )

            task_id = values["id"]
            if task_id in seen_ids:
                raise ValidationError(
                    f"row {line_number}: duplicate nonblank id {task_id!r}"
                )
            seen_ids.add(task_id)
            rows.append({name: values[name] for name in REQUIRED_COLUMNS})
    except csv.Error as exc:
        line_number = getattr(reader, "line_num", "unknown")
        raise ValidationError(f"malformed CSV near row {line_number}: {exc}") from exc

    return {"rows": rows, "counts": {}}


def parse_csv(csv_text: str) -> dict[str, object]:
    """Parse CSV text and return normalized rows plus an empty counts mapping."""

    from io import StringIO

    try:
        return _parse_stream(StringIO(csv_text, newline=""))
    except csv.Error as exc:
        raise ValidationError(f"malformed CSV: {exc}") from exc


def parse_file(input_path: str | Path) -> dict[str, object]:
    """Read and validate a UTF-8 CSV file without changing any destination."""

    path = Path(input_path)
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as stream:
            return _parse_stream(stream)
    except UnicodeError as exc:
        raise ValidationError(f"input CSV is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read input CSV '{path}': {exc}") from exc


# These aliases keep the public seam discoverable for callers that use the
# natural verb "parse" or the earlier file-oriented name.
parse = parse_csv
parse_tasks = parse_file
read_tasks = parse_file


__all__ = [
    "REQUIRED_COLUMNS",
    "ValidationError",
    "parse",
    "parse_csv",
    "parse_file",
    "parse_tasks",
    "read_tasks",
]
