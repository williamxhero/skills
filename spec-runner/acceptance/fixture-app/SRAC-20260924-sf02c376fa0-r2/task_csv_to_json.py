#!/usr/bin/env python3
"""Convert the task CSV format to deterministic JSON."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
from typing import NoReturn


class ValidationError(Exception):
    """An input or CSV validation failure with stable context."""

    def __init__(self, reason: str, row: int | None = None) -> None:
        self.reason = reason
        self.row = row
        super().__init__(reason)


def _fail(prefix: str, path: str, reason: str) -> NoReturn:
    print(f"{prefix}: path={path!r} reason={reason}", file=sys.stderr)
    raise SystemExit(1)


def _read_records(input_path: str) -> list[dict[str, str]]:
    try:
        with open(input_path, "rb") as source:
            raw = source.read()
    except OSError:
        _fail("INPUT_ERROR", input_path, "unreadable")

    if not raw:
        raise ValidationError("empty input", 1)

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValidationError(f"invalid UTF-8 at byte {error.start}", 1) from None

    _validate_quote_syntax(text)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, None)
        if header is None:
            raise ValidationError("empty input", 1)
        if header != ["id", "title", "status"]:
            raise ValidationError("invalid header", 1)

        records: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for row in reader:
            row_number = reader.line_num
            if not row:
                raise ValidationError("blank record", row_number)
            if len(row) != 3:
                raise ValidationError("expected 3 columns", row_number)

            values = [field.strip() for field in row]
            if any(not value for value in values):
                raise ValidationError("empty field", row_number)
            task_id, title, status = values
            if task_id in seen_ids:
                raise ValidationError("duplicate id", row_number)
            seen_ids.add(task_id)
            records.append({"id": task_id, "title": title, "status": status})
        return records
    except csv.Error as error:
        row_number = reader.line_num or 1
        raise ValidationError(f"malformed CSV ({row_number})", row_number) from error


def _validate_quote_syntax(text: str) -> None:
    """Reject quote characters outside standard CSV quoted-field positions."""
    start, unquoted, quoted, closed = range(4)
    state = start
    row_number = 1
    index = 0
    while index < len(text):
        character = text[index]
        if state == quoted:
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                state = closed
            elif character == "\r":
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
                row_number += 1
            elif character == "\n":
                row_number += 1
        elif character == ",":
            state = start
        elif character in "\r\n":
            if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            row_number += 1
            state = start
        elif character == '"':
            if state != start:
                raise ValidationError("malformed CSV quoting", row_number)
            state = quoted
        elif state == start:
            state = unquoted
        elif state == closed:
            raise ValidationError("malformed CSV quoting", row_number)
        index += 1

    if state == quoted:
        raise ValidationError("malformed CSV quoting", row_number)


def _serialize(records: list[dict[str, str]]) -> bytes:
    return (json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _output_parent(output_path: str) -> str:
    return os.path.dirname(os.path.abspath(output_path))


def _check_paths(input_path: str, output_path: str) -> str:
    parent = _output_parent(output_path)
    if not os.path.isdir(parent):
        _fail("OUTPUT_ERROR", output_path, "parent directory does not exist")
    if os.path.isdir(output_path):
        _fail("OUTPUT_ERROR", output_path, "destination is a directory")

    # realpath follows input and output links for relationship checks.  The
    # eventual os.replace still receives output_path, so an output symlink is
    # replaced at that path rather than writing through to its target.
    try:
        if os.path.exists(input_path) and os.path.exists(output_path):
            if os.path.samefile(input_path, output_path):
                _fail("OUTPUT_ERROR", output_path, "input and output are the same file")
    except OSError:
        # The input read below gives the stable input diagnostic when the path
        # cannot be inspected.  A missing output is not an error here.
        pass
    return parent


def _write_atomically(output_path: str, parent: str, content: bytes) -> None:
    temporary_path: str | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=f".{os.path.basename(output_path)}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
        except OSError:
            _fail("OUTPUT_ERROR", output_path, "unable to prepare destination")

        try:
            os.replace(temporary_path, output_path)
        except OSError:
            _fail("OUTPUT_ERROR", output_path, "unable to replace destination")
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="task_csv_to_json.py")
    parser.add_argument("input_csv", metavar="INPUT_CSV")
    parser.add_argument("output_json", metavar="OUTPUT_JSON")
    args = parser.parse_args(argv)

    parent = _check_paths(args.input_csv, args.output_json)
    try:
        records = _read_records(args.input_csv)
        content = _serialize(records)
    except ValidationError as error:
        context = f"row={error.row}" if error.row is not None else "row=1"
        print(f"VALIDATION_ERROR: {context} reason={error.reason}", file=sys.stderr)
        return 2

    _write_atomically(args.output_json, parent, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
