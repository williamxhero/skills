"""Convert a strictly validated task CSV file to compact JSON."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


EXPECTED_HEADER = ["id", "title", "status"]
ALLOWED_STATUSES = {"pending", "in_progress", "done"}


class InputError(Exception):
    """An expected command input or validation error."""


def read_tasks(path: Path) -> list[dict[str, str]]:
    """Read, validate, and return tasks from *path* in input order."""

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as input_file:
            reader = csv.reader(input_file, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise InputError("input CSV is empty; expected header id,title,status") from exc

            if header != EXPECTED_HEADER:
                raise InputError("header must be exactly id,title,status")

            tasks: list[dict[str, str]] = []
            seen_ids: set[str] = set()
            for row_number, row in enumerate(reader, start=2):
                if not row:
                    raise InputError(f"row {row_number} is blank")
                if len(row) != len(EXPECTED_HEADER):
                    raise InputError(
                        f"row {row_number} must contain exactly three columns"
                    )

                values = [value.strip() for value in row]
                if any(not value for value in values):
                    raise InputError(f"row {row_number} contains an empty field")

                task_id, title, status = values
                if task_id in seen_ids:
                    raise InputError(f"row {row_number} contains duplicate id: {task_id}")
                if status not in ALLOWED_STATUSES:
                    raise InputError(
                        f"row {row_number} has unsupported status: {status}"
                    )

                seen_ids.add(task_id)
                tasks.append({"id": task_id, "title": title, "status": status})

            return tasks
    except FileNotFoundError as exc:
        raise InputError(f"input file not found: {path}") from exc
    except IsADirectoryError as exc:
        raise InputError(f"input path is a directory: {path}") from exc
    except PermissionError as exc:
        raise InputError(f"cannot read input file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"input file is not valid UTF-8: {path}") from exc
    except csv.Error as exc:
        raise InputError(f"malformed CSV: {exc}") from exc
    except OSError as exc:
        raise InputError(f"cannot read input file {path}: {exc}") from exc


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a task CSV file to compact JSON."
    )
    parser.add_argument("input_csv", help="path to the input CSV file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_arguments(argv)
        tasks = read_tasks(Path(arguments.input_csv))
        output = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sys.stdout.buffer.write(output.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
