"""Convert a strictly validated task CSV file to deterministic JSON."""

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


REQUIRED_COLUMNS = ("id", "title", "status")


class ConversionError(Exception):
    """An expected input, serialization, or output failure."""


def _validate_quote_syntax(text: str) -> None:
    state = "start"
    line = 1
    index = 0
    while index < len(text):
        character = text[index]
        if state == "quoted":
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                state = "closed"
            elif character in "\r\n":
                if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
                line += 1
        elif character == ',':
            state = "start"
        elif character in "\r\n":
            if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            line += 1
            state = "start"
        elif character == '"':
            if state != "start":
                raise ConversionError(f"malformed CSV quoting near row {line}")
            state = "quoted"
        elif state == "start":
            state = "unquoted"
        elif state == "closed":
            raise ConversionError(f"malformed CSV quoting near row {line}")
        index += 1
    if state == "quoted":
        raise ConversionError(f"malformed CSV quoting near row {line}")


def read_tasks(input_path: Path) -> list[dict[str, str]]:
    """Read and validate the complete CSV before returning any tasks."""

    tasks: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    try:
        source = input_path.open("r", encoding="utf-8", errors="strict", newline="")
    except OSError as exc:
        raise ConversionError(f"cannot open input CSV '{input_path}': {exc}") from exc

    try:
        text = source.read()
        _validate_quote_syntax(text)
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ConversionError("input CSV is empty; expected header id,title,status") from exc

        positions: dict[str, int] = {}
        duplicates: list[str] = []
        for required in REQUIRED_COLUMNS:
            matches = [index for index, value in enumerate(header) if value == required]
            if len(matches) > 1:
                duplicates.append(required)
            elif matches:
                positions[required] = matches[0]
        if duplicates:
            raise ConversionError(f"duplicate required column(s): {', '.join(duplicates)}")
        missing = [required for required in REQUIRED_COLUMNS if required not in positions]
        if missing:
            raise ConversionError(f"missing required column(s): {', '.join(missing)}")

        for row in reader:
            line = reader.line_num
            if not row:
                raise ConversionError(f"row {line}: blank rows are not allowed")
            if any(index >= len(row) for index in positions.values()):
                raise ConversionError(f"row {line}: missing value for a required column")

            values = {name: row[index] for name, index in positions.items()}
            if any(not values[name].strip() for name in REQUIRED_COLUMNS):
                blank = next(name for name in REQUIRED_COLUMNS if not values[name].strip())
                raise ConversionError(f"row {line}: {blank} must not be empty")
            task_id, title, status = (values[name] for name in REQUIRED_COLUMNS)
            if task_id in seen_ids:
                raise ConversionError(f"row {line}: duplicate id '{task_id}'")

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
    try:
        return (document + "\n").encode("utf-8")
    except UnicodeError as exc:
        raise ConversionError(f"could not encode output JSON: {exc}") from exc


def atomic_write(output_path: Path, content: bytes) -> None:
    """Prepare content beside the destination, then atomically replace it."""

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
    atomic_write(output_path, serialize_tasks(tasks))


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
