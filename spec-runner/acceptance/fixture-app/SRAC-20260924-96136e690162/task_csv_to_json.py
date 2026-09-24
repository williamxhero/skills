"""Convert a strictly validated task CSV file to canonical JSON."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence


HEADER = ["id", "title", "status"]
ALLOWED_STATUSES = {"todo", "doing", "done"}


class ConversionError(Exception):
    """An expected input, serialization, or output failure."""


def read_tasks(input_path: Path) -> list[dict[str, str]]:
    """Read and validate the complete CSV before returning any tasks."""

    tasks: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    try:
        source = input_path.open("r", encoding="utf-8", errors="strict", newline="")
    except OSError as exc:
        raise ConversionError(f"cannot open input CSV '{input_path}': {exc}") from exc

    try:
        reader = csv.reader(source, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ConversionError("input CSV is empty; expected header id,title,status") from exc

        if header != HEADER:
            raise ConversionError(
                f"invalid CSV header: expected {','.join(HEADER)}, got {','.join(header)}"
            )

        for row in reader:
            line = reader.line_num
            if not row or all(value == "" for value in row):
                raise ConversionError(f"row {line}: blank rows are not allowed")
            if len(row) != len(HEADER):
                raise ConversionError(
                    f"row {line}: expected exactly 3 columns, got {len(row)}"
                )

            task_id = row[0].strip()
            title = row[1].strip()
            status = row[2]
            if not task_id:
                raise ConversionError(f"row {line}: id must not be empty")
            if not title:
                raise ConversionError(f"row {line}: title must not be empty")
            if task_id in seen_ids:
                raise ConversionError(f"row {line}: duplicate id '{task_id}'")
            if status not in ALLOWED_STATUSES:
                raise ConversionError(
                    f"row {line}: unsupported status '{status}'; "
                    "expected todo, doing, or done"
                )

            seen_ids.add(task_id)
            tasks.append({"id": task_id, "title": title, "status": status})
    except csv.Error as exc:
        line = getattr(reader, "line_num", "unknown")
        raise ConversionError(f"malformed CSV near row {line}: {exc}") from exc
    except UnicodeError as exc:
        raise ConversionError(f"input CSV is not valid UTF-8: {exc}") from exc
    finally:
        source.close()

    return tasks


def serialize_tasks(tasks: list[dict[str, str]]) -> bytes:
    """Serialize tasks using the stable, compact UTF-8 output contract."""

    try:
        document = json.dumps(
            {"tasks": tasks},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConversionError(f"could not serialize output JSON: {exc}") from exc
    return (document + "\n").encode("utf-8")


def atomic_write(output_path: Path, content: bytes) -> None:
    """Write content beside the destination, then atomically replace it."""

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
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
        raise ConversionError(f"cannot replace output JSON '{output_path}': {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def convert(input_path: Path, output_path: Path) -> None:
    """Validate, serialize, and atomically write one complete conversion."""

    tasks = read_tasks(input_path)
    content = serialize_tasks(tasks)
    atomic_write(output_path, content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a strictly validated task CSV to deterministic JSON."
    )
    parser.add_argument("input_csv", type=Path, help="source UTF-8 task CSV")
    parser.add_argument("output_json", type=Path, help="destination JSON file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        convert(args.input_csv, args.output_json)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
