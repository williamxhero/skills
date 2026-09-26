"""Convert validated task CSV data into deterministic JSON."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path


REQUIRED_COLUMNS = ["id", "title", "status"]
VALID_STATUSES = {"todo", "doing", "done"}


class ValidationError(ValueError):
    """Raised when the CSV does not satisfy the task data contract."""


def load_tasks(input_path: Path) -> list[dict[str, str]]:
    """Read and validate all tasks from *input_path*."""

    tasks: list[dict[str, str]] = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as input_file:
        reader = csv.reader(input_file, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValidationError("CSV input must contain the required header") from exc

        if header != REQUIRED_COLUMNS:
            raise ValidationError(
                "CSV header must be exactly: " + ",".join(REQUIRED_COLUMNS)
            )

        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(REQUIRED_COLUMNS):
                raise ValidationError(
                    f"row {row_number} must contain exactly {len(REQUIRED_COLUMNS)} values"
                )

            task = dict(zip(REQUIRED_COLUMNS, row))
            if not task["id"].strip():
                raise ValidationError(f"row {row_number} has an empty id")
            if not task["title"].strip():
                raise ValidationError(f"row {row_number} has an empty title")
            if task["status"] not in VALID_STATUSES:
                raise ValidationError(f"row {row_number} has an invalid status")
            tasks.append(task)

    return tasks


def serialize_tasks(tasks: list[dict[str, str]]) -> str:
    """Serialize tasks with stable field ordering, formatting, and encoding."""

    return json.dumps(
        {"tasks": tasks},
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    ) + "\n"


def write_atomically(output_path: Path, content: str) -> None:
    """Replace *output_path* only after the complete content is ready."""

    output_directory = output_path.parent
    if not output_directory.is_dir():
        raise OSError(f"output directory does not exist: {output_directory}")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_directory,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, dest="input_path")
    parser.add_argument("--output", required=True, type=Path, dest="output_path")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        tasks = load_tasks(args.input_path)
        content = serialize_tasks(tasks)
        write_atomically(args.output_path, content)
    except (OSError, UnicodeError, csv.Error, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
