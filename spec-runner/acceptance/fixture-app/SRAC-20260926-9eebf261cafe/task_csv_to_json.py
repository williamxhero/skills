"""Convert a task CSV file into deterministic JSON.

The command is intentionally exposed as a module so it can be run with::

    python -m task_csv_to_json INPUT_CSV OUTPUT_JSON
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence


EXPECTED_HEADER = ["id", "title", "status"]
VALID_STATUSES = {"todo", "in_progress", "done"}


class ConversionError(Exception):
    """An expected, user-facing conversion failure."""


def _validate_csv_quotes(text: str) -> None:
    """Reject quote characters that cannot occur in RFC-style CSV fields."""
    in_quotes = False
    field_start = True
    after_closing_quote = False
    index = 0

    while index < len(text):
        character = text[index]
        if in_quotes:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                in_quotes = False
                after_closing_quote = True
            index += 1
            continue

        if character == '"':
            if not field_start:
                raise ConversionError("malformed CSV: quote in an unquoted field")
            in_quotes = True
            field_start = False
        elif character == ",":
            field_start = True
            after_closing_quote = False
        elif character in "\r\n":
            field_start = True
            after_closing_quote = False
        elif after_closing_quote:
            if character not in " \t":
                raise ConversionError(
                    "malformed CSV: characters after a closing quote"
                )
        elif not (field_start and character in " \t"):
            field_start = False
        index += 1

    if in_quotes:
        raise ConversionError("malformed CSV: unterminated quoted field")


def _read_tasks(input_path: Path) -> list[dict[str, str]]:
    try:
        with input_path.open("r", encoding="utf-8", newline="") as source:
            text = source.read()
    except UnicodeDecodeError as exc:
        raise ConversionError(f"input CSV is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ConversionError(f"cannot read input CSV '{input_path}': {exc}") from exc

    if not text:
        raise ConversionError("input CSV is empty")

    _validate_csv_quotes(text)
    # The explicit quote scan rejects malformed embedded quotes. The standard
    # reader then parses valid quoted fields while allowing surrounding
    # whitespace that the command trims from every value.
    reader = csv.reader(io.StringIO(text), strict=False, skipinitialspace=True)
    try:
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ConversionError("input CSV is empty") from exc

        if header != EXPECTED_HEADER:
            raise ConversionError(
                "CSV header must be exactly: id,title,status"
            )

        tasks: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if not row or all(not value.strip() for value in row):
                raise ConversionError(f"blank data row at line {row_number}")
            if len(row) != len(EXPECTED_HEADER):
                raise ConversionError(
                    f"row {row_number} must contain exactly 3 fields"
                )

            task_id, title, status = (value.strip() for value in row)
            if not task_id:
                raise ConversionError(f"row {row_number} has an empty id")
            if not title:
                raise ConversionError(f"row {row_number} has an empty title")
            if status not in VALID_STATUSES:
                raise ConversionError(
                    f"row {row_number} has invalid status '{status}'"
                )
            if task_id in seen_ids:
                raise ConversionError(
                    f"duplicate id '{task_id}' at row {row_number}"
                )

            seen_ids.add(task_id)
            tasks.append({"id": task_id, "title": title, "status": status})
    except csv.Error as exc:
        raise ConversionError(f"malformed CSV: {exc}") from exc

    return tasks


def _serialize(tasks: list[dict[str, str]]) -> bytes:
    text = json.dumps(
        {"tasks": tasks},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    return text.encode("utf-8")


def _write_atomically(output_path: Path, content: bytes) -> None:
    parent = output_path.parent
    if not parent.is_dir():
        raise ConversionError(
            f"output directory does not exist: '{parent}'"
        )

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    except OSError as exc:
        raise ConversionError(f"cannot write output JSON '{output_path}': {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                # The original failure is more useful to the caller than a
                # cleanup failure, and the temporary file is best-effort.
                pass


def convert(input_path: str | os.PathLike[str], output_path: str | os.PathLike[str]) -> None:
    """Validate, serialize, and atomically deliver one task CSV conversion."""
    tasks = _read_tasks(Path(input_path))
    _write_atomically(Path(output_path), _serialize(tasks))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m task_csv_to_json",
        description="Convert a task CSV file to deterministic JSON.",
    )
    parser.add_argument("input_csv", metavar="INPUT_CSV")
    parser.add_argument("output_json", metavar="OUTPUT_JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        convert(args.input_csv, args.output_json)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
