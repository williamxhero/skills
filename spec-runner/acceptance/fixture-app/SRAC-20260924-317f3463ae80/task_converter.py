"""Convert validated task CSV data into deterministic JSON."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, TextIO


REQUIRED_HEADERS = ("id", "title", "status")


class ValidationError(ValueError):
    """A user-facing CSV conversion or output failure."""


ConversionError = ValidationError


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError(message)


def _validate_csv_quoting(text: str) -> None:
    """Reject quotes outside the CSV escaped-field grammar."""

    field_start, unquoted, quoted, quote_closed = range(4)
    state = field_start
    index = 0
    while index < len(text):
        character = text[index]

        if state == field_start:
            if character == '"':
                state = quoted
            elif character == ",":
                pass
            elif character in "\r\n":
                state = field_start
            else:
                state = unquoted
        elif state == unquoted:
            if character == '"':
                raise ValidationError("malformed CSV quoting")
            if character == ",":
                state = field_start
            elif character == "\r":
                state = field_start
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
            elif character == "\n":
                state = field_start
        elif state == quoted:
            if character == '"':
                state = quote_closed
        else:
            if character == '"':
                state = quoted
            elif character == ",":
                state = field_start
            elif character == "\r":
                state = field_start
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
            elif character == "\n":
                state = field_start
            else:
                raise ValidationError("malformed CSV quoting")

        index += 1

    if state == quoted:
        raise ValidationError("malformed CSV quoting")


def _read_records(text: str) -> list[list[str]]:
    _validate_csv_quoting(text)
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValidationError("malformed CSV quoting") from exc


def convert_csv(csv_text: str | TextIO) -> dict[str, Any]:
    """Validate CSV text and return rows plus lexicographically sorted counts."""

    if hasattr(csv_text, "read"):
        csv_text = csv_text.read()
    if not isinstance(csv_text, str):
        raise TypeError("csv input must be text")

    records = _read_records(csv_text)
    if not records:
        raise ValidationError("missing header row")

    headers = records[0]
    positions: dict[str, int] = {}
    for required in REQUIRED_HEADERS:
        occurrences = headers.count(required)
        if occurrences == 0:
            raise ValidationError(f"missing required header: {required}")
        if occurrences > 1:
            raise ValidationError(f"duplicate required header: {required}")
        positions[required] = headers.index(required)

    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    counts: dict[str, int] = {}
    for row_number, record in enumerate(records[1:], start=2):
        if not record:
            raise ValidationError(f"blank row at row {row_number}")
        if len(record) != len(headers):
            raise ValidationError(f"malformed row at row {row_number}")

        row = {name: record[positions[name]] for name in REQUIRED_HEADERS}
        for name, value in row.items():
            if not value or value.isspace():
                raise ValidationError(
                    f"blank required value for {name} at row {row_number}"
                )

        task_id = row["id"]
        if task_id in seen_ids:
            raise ValidationError(f"duplicate nonblank id at row {row_number}")
        seen_ids.add(task_id)
        rows.append(row)
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1

    return {"rows": rows, "counts": {key: counts[key] for key in sorted(counts)}}


parse_csv = convert_csv
normalize_csv = convert_csv
convert = convert_csv


def convert_file(input_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a UTF-8 CSV file and validate it before returning its result."""

    try:
        with open(input_path, "r", encoding="utf-8-sig", newline="") as source:
            return convert_csv(source)
    except UnicodeError as exc:
        raise ValidationError("input is not valid UTF-8") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read input: {exc.strerror or exc}") from exc


parse_file = convert_file
parse_tasks = convert_file
read_tasks = convert_file


def _serialize(result: dict[str, Any]) -> str:
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except Exception as exc:
        raise ValidationError("could not serialize JSON output") from exc


def write_result(result: dict[str, Any], output_path: str | os.PathLike[str]) -> None:
    """Serialize and atomically replace an output file after full preparation."""

    destination = Path(output_path)
    parent = destination.parent
    try:
        if not parent.exists():
            raise ValidationError("output directory does not exist")
        if not parent.is_dir():
            raise ValidationError("output parent is not a directory")
        if destination.is_dir():
            raise ValidationError("output path is a directory")
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError("could not access output path") from exc

    content = _serialize(result)
    temporary_path: Path | None = None
    try:
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
        except Exception as exc:
            raise ValidationError("could not prepare or replace output") from exc

        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            # fdopen may fail before ownership transfers to the file object.
            # Closing here is harmless after a context-manager failure and
            # prevents a descriptor leak on that earlier failure path.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

        try:
            os.replace(temporary_path, destination)
        except Exception as exc:
            raise ValidationError("could not prepare or replace output") from exc
        temporary_path = None
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("could not prepare or replace output") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def convert_csv_to_json(
    input_path: str | os.PathLike[str], output_path: str | os.PathLike[str]
) -> dict[str, Any]:
    """Validate, serialize, and atomically replace one conversion result."""

    result = convert_file(input_path)
    write_result(result, output_path)
    return result


atomic_write = write_result


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Convert task CSV data to JSON")
    parser.add_argument("--input", dest="input_path")
    parser.add_argument("--output", dest="output_path")
    parser.add_argument("input_positional", nargs="?")
    parser.add_argument("output_positional", nargs="?")
    return parser


def main(arguments: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(arguments)
        input_path = args.input_path or args.input_positional
        output_path = args.output_path or args.output_positional
        if not input_path or not output_path:
            raise ValidationError("expected input and output paths")
        if (args.input_path is None) != (args.output_path is None):
            raise ValidationError("input and output options must be provided together")
        if args.input_positional is not None or args.output_positional is not None:
            if args.input_path is not None or args.output_path is not None:
                raise ValidationError("use either options or positional paths")

        convert_csv_to_json(input_path, output_path)
    except Exception as exc:
        detail = " ".join(str(exc).splitlines()).strip() or "conversion failed"
        print(f"error: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
