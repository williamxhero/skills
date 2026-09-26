"""Convert a strictly validated task CSV file to deterministic JSON."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


USAGE = "usage: python task_csv_to_json.py INPUT_CSV OUTPUT_JSON"
HEADER = ["id", "title", "status"]


class InputFormatError(Exception):
    """The input exists but is not a valid task CSV."""


def fail(message: str, exit_code: int) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def paths_are_same(input_path: Path, output_path: Path) -> bool:
    """Return whether the two paths identify the same file."""
    try:
        if input_path.resolve() == output_path.resolve():
            return True
    except (OSError, RuntimeError, ValueError):
        # The later input/output operation reports the appropriate I/O error.
        pass

    if output_path.exists():
        try:
            return os.path.samefile(input_path, output_path)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            pass
    return False


def read_tasks(input_path: Path) -> list[dict[str, str]]:
    try:
        raw = input_path.read_bytes()
    except (OSError, ValueError) as exc:
        raise OSError from exc

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputFormatError from exc

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise InputFormatError from exc
    except csv.Error as exc:
        raise InputFormatError from exc

    if header != HEADER:
        raise InputFormatError

    tasks: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    try:
        for row in reader:
            if len(row) != len(HEADER):
                raise InputFormatError

            values = [value.strip() for value in row]
            if any(not value for value in values):
                raise InputFormatError

            task_id, title, status = values
            if task_id in seen_ids:
                raise InputFormatError
            seen_ids.add(task_id)
            tasks.append({"id": task_id, "title": title, "status": status})
    except csv.Error as exc:
        raise InputFormatError from exc

    return tasks


def serialize_tasks(tasks: list[dict[str, str]]) -> bytes:
    try:
        serialized = json.dumps(
            tasks,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (serialized + "\n").encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise InputFormatError from exc


def write_atomically(output_path: Path, content: bytes) -> None:
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, output_path)
        temporary_path = None
    except (OSError, ValueError) as exc:
        raise OSError from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        fail(USAGE, 2)

    input_path = Path(argv[1])
    output_path = Path(argv[2])
    if paths_are_same(input_path, output_path):
        fail("error: input and output paths must differ", 2)

    try:
        tasks = read_tasks(input_path)
        content = serialize_tasks(tasks)
    except InputFormatError:
        fail("error: invalid input CSV", 2)
    except (OSError, ValueError):
        fail("error: unable to read input CSV", 1)

    try:
        write_atomically(output_path, content)
    except (OSError, ValueError):
        fail("error: unable to write output JSON", 1)

    return 0


if __name__ == "__main__":
    main(sys.argv)
