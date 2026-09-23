"""Validate a task CSV and convert it to deterministic JSON.

Usage: python tasks_csv.py INPUT.csv [OUTPUT.json]
If OUTPUT is omitted, JSON is written to standard output.
"""

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path


FIELDS = ("id", "title", "status")


def read_tasks(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("CSV header must be exactly: id,title,status")

        tasks = []
        seen_ids = set()
        for line, row in enumerate(reader, start=2):
            if None in row or any(row[field] is None for field in FIELDS):
                raise ValueError(f"row {line}: expected exactly three columns")
            task = {field: row[field].strip() for field in FIELDS}
            if any(not value for value in task.values()):
                raise ValueError(f"row {line}: id, title and status must be nonempty")
            if task["id"] in seen_ids:
                raise ValueError(f"row {line}: duplicate id {task['id']!r}")
            seen_ids.add(task["id"])
            tasks.append(task)
    return tasks


def write_atomic(path: Path, content: str) -> None:
    # A failed write must leave an existing result intact.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="task CSV")
    parser.add_argument("output", type=Path, nargs="?", help="JSON destination (default: stdout)")
    args = parser.parse_args(argv)

    try:
        tasks = read_tasks(args.input)
        content = json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            sys.stdout.write(content)
        else:
            write_atomic(args.output, content)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
