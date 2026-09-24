"""Convert task CSV data into the normalized task-row contract.

The conversion functions in this module deliberately keep parsing and
validation separate from destination-file handling.  That lets callers use
the core conversion seam without having to create a file, while the command
line entry point provides the same validation guarantees for file inputs.
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


REQUIRED_HEADERS = ("id", "title", "status")


class ValidationError(ValueError):
    """A concise, user-facing failure in the CSV conversion contract."""


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser that reports usage errors through the CLI contract."""

    def error(self, message: str) -> None:
        raise ValidationError(message)


def _parse_rows(csv_text: str) -> list[list[str]]:
    """Parse CSV text using strict RFC-style quoting checks."""

    try:
        reader = csv.reader(
            io.StringIO(csv_text, newline=""),
            delimiter=",",
            strict=True,
        )
        return list(reader)
    except csv.Error as exc:
        raise ValidationError(f"malformed CSV: {exc}") from None


def convert_csv(csv_text: str) -> dict[str, object]:
    """Validate and normalize CSV text.

    Required headers are matched exactly, but may occur in any order.  Values
    are returned exactly as parsed, including surrounding whitespace.  No
    output is returned until every row has passed validation.
    """

    if not isinstance(csv_text, str):
        raise TypeError("csv_text must be a string")

    parsed = _parse_rows(csv_text)
    if not parsed:
        raise ValidationError("missing header row")

    headers = parsed[0]
    if len(headers) != len(set(headers)):
        raise ValidationError("duplicate header")

    missing = [header for header in REQUIRED_HEADERS if header not in headers]
    if missing:
        raise ValidationError(f"missing required header: {missing[0]}")

    positions = {header: headers.index(header) for header in REQUIRED_HEADERS}
    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for row_number, values in enumerate(parsed[1:], start=2):
        if not values or all(value == "" for value in values):
            raise ValidationError(f"blank row at line {row_number}")

        normalized: dict[str, str] = {}
        for field in REQUIRED_HEADERS:
            position = positions[field]
            value = values[position] if position < len(values) else ""
            if not value.strip():
                raise ValidationError(
                    f"blank required value for {field} at line {row_number}"
                )
            normalized[field] = value

        task_id = normalized["id"]
        if task_id in seen_ids:
            raise ValidationError(f"duplicate id at line {row_number}: {task_id}")
        seen_ids.add(task_id)
        rows.append(normalized)

    status_frequencies: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        status_frequencies[status] = status_frequencies.get(status, 0) + 1
    counts = {
        status: status_frequencies[status]
        for status in sorted(status_frequencies)
    }

    return {"rows": rows, "counts": counts}


# These aliases keep the core seam discoverable without creating alternate
# parsing paths.
normalize_csv = convert_csv
convert = convert_csv


def convert_file(input_path: str | os.PathLike[str]) -> dict[str, object]:
    """Read a UTF-8 CSV file and return its validated normalized result."""

    try:
        with open(input_path, "r", encoding="utf-8", newline="") as source:
            return convert_csv(source.read())
    except UnicodeDecodeError:
        raise ValidationError("input is not valid UTF-8") from None
    except OSError as exc:
        raise ValidationError(f"cannot read input: {exc.strerror or exc}") from None


def write_result(result: dict[str, object], output_path: str | os.PathLike[str]) -> None:
    """Atomically replace *output_path* with a complete JSON result."""

    destination = Path(output_path)
    parent = destination.parent
    if not parent.is_dir():
        raise ValidationError("output directory does not exist")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(result, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    except Exception as exc:
        detail = getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__
        raise ValidationError(f"cannot write output: {detail}") from None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Normalize task CSV data")
    parser.add_argument("--input", required=True, help="UTF-8 CSV input path")
    parser.add_argument("--output", required=True, help="JSON output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the file conversion CLI, returning a process exit code."""

    try:
        args = _build_parser().parse_args(argv)
        result = convert_file(args.input)
        write_result(result, args.output)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        detail = getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__
        print(f"error: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
