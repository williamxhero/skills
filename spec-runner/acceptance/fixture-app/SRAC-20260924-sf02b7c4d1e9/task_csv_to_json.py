"""Convert a task CSV file to deterministic JSON.

The command is intentionally small and exposes only the documented CLI:
``python task_csv_to_json.py INPUT_CSV OUTPUT_JSON``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


VALID_STATUSES = {"pending", "in_progress", "done"}
EXPECTED_HEADER = ["id", "title", "status"]


def fail(message: str, exit_code: int) -> NoReturn:
    """Write one stable diagnostic and terminate without writing stdout."""

    sys.stderr.write(f"error: {message}\n")
    raise SystemExit(exit_code)


def usage() -> NoReturn:
    fail("usage: python task_csv_to_json.py INPUT_CSV OUTPUT_JSON", 2)


def read_and_convert(input_path: Path) -> bytes:
    """Read, validate, and serialize the complete input before any output IO."""

    try:
        raw = input_path.read_bytes()
    except OSError:
        fail(f"operational: cannot read input", 1)

    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        fail("input-format: invalid UTF-8", 2)

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        try:
            header = next(reader)
        except StopIteration:
            fail("validation: header: expected id,title,status", 2)

        if len(header) != len(EXPECTED_HEADER):
            fail("input-format: row 2: expected 3 columns", 2)
        if header != EXPECTED_HEADER:
            fail("validation: header: expected id,title,status", 2)

        records: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        logical_row = 1  # Header is record 1; physical line count is separate.
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                line = max(reader.line_num, 1)
                fail(f"input-format: row {line}: malformed CSV", 2)

            logical_row += 1
            if not row:
                fail(f"validation: row {logical_row}: blank row", 2)
            if len(row) != 3:
                fail(f"input-format: row {logical_row}: expected 3 columns", 2)

            values = [value.strip() for value in row]
            for column, value in zip(EXPECTED_HEADER, values):
                if not value:
                    fail(f"validation: row {logical_row}, column {column}: blank field", 2)

            task_id, title, status = values
            if task_id in seen_ids:
                fail(f"validation: row {logical_row}, column id: duplicate id", 2)
            if status not in VALID_STATUSES:
                fail(f"validation: row {logical_row}, column status: invalid status", 2)

            seen_ids.add(task_id)
            records.append({"id": task_id, "title": title, "status": status})
    except csv.Error:
        line = max(reader.line_num, 1)
        fail(f"input-format: row {line}: malformed CSV", 2)

    try:
        return (json.dumps(records, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError, UnicodeError):
        # This is defensive: all values originated in a successfully decoded CSV.
        fail("input-format: unable to serialize JSON", 2)


def same_file(input_path: Path, output_path: Path) -> bool:
    """Compare paths without requiring the destination to exist."""

    try:
        if output_path.exists() and input_path.exists():
            return os.path.samefile(input_path, output_path)
    except OSError:
        # Fall through to normalized absolute paths for inaccessible paths.
        pass
    return os.path.normcase(os.path.abspath(input_path)) == os.path.normcase(
        os.path.abspath(output_path)
    )


def publish(output_path: Path, payload: bytes) -> None:
    """Atomically replace the destination using a destination-local temporary."""

    parent = output_path.parent
    if not parent.is_dir():
        fail("operational: output parent does not exist", 1)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{output_path.name}.", suffix=".tmp", dir=parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output_path)
        temporary_name = None
    except OSError:
        fail("operational: cannot write output", 1)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        usage()

    input_path = Path(args[0])
    output_path = Path(args[1])
    if same_file(input_path, output_path):
        fail("validation: input and output must be different files", 2)

    payload = read_and_convert(input_path)
    publish(output_path, payload)
    return 0


if __name__ == "__main__":
    main()
