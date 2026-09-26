"""Convert a strictly validated task CSV file to deterministic JSON."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import NoReturn


EXPECTED_HEADER = ["id", "title", "status"]
USAGE = "usage: task_csv_to_json.py INPUT.csv OUTPUT.json"


class ConversionError(Exception):
    """A user-facing conversion failure with a concise diagnostic."""


def fail(message: str) -> NoReturn:
    raise ConversionError(message)


def is_record_separator(text: str, index: int) -> bool:
    return text[index] == "\n" or text[index] == "\r"


def next_after_record_separator(text: str, index: int) -> int:
    if text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
        return index + 2
    return index + 1


def validate_csv_quotes(text: str) -> None:
    row = 1
    column = 1
    index = 0
    field_start = True
    in_quotes = False
    after_quote = False

    while index < len(text):
        character = text[index]

        if in_quotes:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    column += 2
                    continue
                in_quotes = False
                after_quote = True
            elif is_record_separator(text, index):
                index = next_after_record_separator(text, index)
                row += 1
                column = 1
                continue
            column += 1
            index += 1
            continue

        if after_quote:
            if character == ",":
                field_start = True
                after_quote = False
                column += 1
                index += 1
                continue
            if is_record_separator(text, index):
                index = next_after_record_separator(text, index)
                row += 1
                column = 1
                field_start = True
                after_quote = False
                continue
            fail(f'CSV syntax error at row {row}, column {column}: expected delimiter after quote')

        if character == '"':
            if field_start:
                in_quotes = True
                field_start = False
                column += 1
                index += 1
                continue
            fail(f'CSV syntax error at row {row}, column {column}: unescaped quote')

        if character == ",":
            field_start = True
            column += 1
            index += 1
            continue

        if is_record_separator(text, index):
            index = next_after_record_separator(text, index)
            row += 1
            column = 1
            field_start = True
            continue

        field_start = False
        column += 1
        index += 1

    if in_quotes:
        fail(f"CSV syntax error at row {row}, column {column}: unterminated quoted field")


def same_path(input_path: Path, output_path: Path) -> bool:
    """Return whether the two arguments identify the same filesystem path."""
    input_absolute = os.path.realpath(os.path.abspath(input_path))
    output_absolute = os.path.realpath(os.path.abspath(output_path))
    if os.path.normcase(input_absolute) == os.path.normcase(output_absolute):
        return True

    try:
        return input_path.exists() and output_path.exists() and os.path.samefile(
            input_path, output_path
        )
    except OSError:
        return False


def read_records(input_path: Path) -> list[dict[str, str]]:
    try:
        raw = input_path.read_bytes()
    except OSError as exc:
        fail(f"input I/O error: {exc.strerror or 'unable to read input'}")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        fail(f"input encoding error at byte {exc.start}: expected UTF-8")

    validate_csv_quotes(text)

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        fail("validation error: input is empty; expected header id,title,status")
    except csv.Error as exc:
        line = reader.line_num or 1
        fail(f"CSV syntax error at row {line}: {exc}")

    if header != EXPECTED_HEADER:
        fail("validation error at row 1: header must be exactly id,title,status")

    records: list[dict[str, str]] = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if not row:
                fail(f"validation error at row {row_number}: blank data row")
            if len(row) != len(EXPECTED_HEADER):
                fail(
                    f"validation error at row {row_number}: "
                    f"expected 3 fields, found {len(row)}"
                )

            normalized = [value.strip() for value in row]
            for column_number, value in enumerate(normalized, start=1):
                if not value:
                    fail(
                        f"validation error at row {row_number}, "
                        f"column {column_number}: value is empty after trimming"
                    )

            records.append(
                {
                    "id": normalized[0],
                    "title": normalized[1],
                    "status": normalized[2],
                }
            )
    except csv.Error as exc:
        line = reader.line_num or 1
        fail(f"CSV syntax error at row {line}: {exc}")

    return records


def serialize(records: list[dict[str, str]]) -> bytes:
    try:
        rendered = json.dumps(
            records,
            ensure_ascii=False,
            indent=2,
            separators=(",", ": "),
        )
    except (TypeError, ValueError) as exc:
        fail(f"serialization error: {exc}")
    return (rendered + "\n").encode("utf-8")


def install_atomically(output_path: Path, payload: bytes) -> None:
    parent = output_path.parent
    if not parent.is_dir():
        fail(f"output I/O error: parent directory does not exist: {parent}")

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    except OSError as exc:
        fail(f"output I/O error: {exc.strerror or 'unable to write output'}")
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def convert(input_argument: str, output_argument: str) -> None:
    input_path = Path(input_argument)
    output_path = Path(output_argument)

    if same_path(input_path, output_path):
        fail("validation error: input and output paths must be different")

    records = read_records(input_path)
    payload = serialize(records)
    install_atomically(output_path, payload)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        convert(arguments[0], arguments[1])
    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
