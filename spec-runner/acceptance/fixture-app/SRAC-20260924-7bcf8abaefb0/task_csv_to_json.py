"""Convert a strictly validated task CSV file to deterministic JSON.

The module is intentionally usable both as a script and as a module:
``python task_csv_to_json.py --input tasks.csv --output tasks.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Iterable


HEADERS = ("id", "title", "status")
ALLOWED_STATUSES = frozenset({"todo", "doing", "done"})


class ConversionError(Exception):
    """A user-facing conversion failure."""


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except (OSError, ValueError):
        return False


def _validate_paths(input_path: Path, output_path: Path) -> Path:
    """Validate path policy and return the output's existing parent."""

    if not _is_regular_file(input_path):
        raise ConversionError("input must be a readable regular file")
    if not os.access(input_path, os.R_OK):
        raise ConversionError("input is not readable")

    try:
        output_exists = os.path.lexists(output_path)
    except (OSError, ValueError) as exc:
        raise ConversionError(f"cannot inspect output: {exc}") from exc

    if output_exists and not _is_regular_file(output_path):
        raise ConversionError("output must be a regular file")
    if output_exists and not os.access(output_path, os.W_OK):
        raise ConversionError("output is not writable")

    parent = output_path.parent
    if not parent.is_dir():
        raise ConversionError("output parent directory does not exist")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ConversionError("output parent directory is not writable")

    try:
        if os.path.samefile(input_path, output_path):
            raise ConversionError("input and output must be different files")
    except FileNotFoundError:
        # A missing output is valid; the input still exists by this point.
        pass
    except OSError:
        # Compare normalized absolute paths for platforms where samefile is
        # unavailable or cannot compare the paths.
        if os.path.normcase(str(input_path.resolve())) == os.path.normcase(
            str(output_path.resolve())
        ):
            raise ConversionError("input and output must be different files")

    return parent


def _iter_rows(reader: Iterable[list[str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for row_number, values in enumerate(reader, start=2):
        if not values or all(value == "" for value in values):
            raise ConversionError(f"blank row at line {row_number}")
        if len(values) != len(HEADERS):
            raise ConversionError(f"expected 3 fields at line {row_number}")

        task_id, title, status = (value.strip() for value in values)
        if not task_id or not title or not status:
            raise ConversionError(f"required value is blank at line {row_number}")
        if task_id in seen_ids:
            raise ConversionError(f"duplicate id at line {row_number}")
        if status not in ALLOWED_STATUSES:
            raise ConversionError(f"unsupported status at line {row_number}")

        seen_ids.add(task_id)
        rows.append({"id": task_id, "title": title, "status": status})

    return rows


def _read_tasks(input_path: Path) -> list[dict[str, str]]:
    try:
        with input_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            try:
                headers = next(reader)
            except StopIteration as exc:
                raise ConversionError("input is missing the header row") from exc
            if headers != list(HEADERS):
                raise ConversionError("header must be exactly id,title,status")
            return _iter_rows(reader)
    except UnicodeDecodeError as exc:
        raise ConversionError("input is not valid UTF-8") from exc
    except csv.Error as exc:
        raise ConversionError(f"malformed CSV: {exc}") from exc
    except OSError as exc:
        raise ConversionError(f"cannot read input: {exc}") from exc


def _failure_mode() -> str | None:
    """Read the test-only injection switch.

    The primary name is deliberately explicit. The aliases make the seam
    convenient for command-level harnesses without affecting normal runs.
    """

    for name in (
        "TASK_CSV_TO_JSON_TEST_FAILURE",
        "CSV_TO_JSON_TEST_FAILURE",
        "TASK_CSV_JSON_FAILURE",
        "CSV_TO_JSON_INJECT_FAILURE",
        "TASK_CSV_TO_JSON_FAILURE",
    ):
        value = os.environ.get(name)
        if value:
            return value.strip().lower()
    return None


def _write_transactionally(output_path: Path, payload: bytes, parent: Path) -> None:
    temporary_path: Path | None = None
    try:
        mode = _failure_mode()
        if mode in {"prepare", "preparation", "output-preparation"}:
            raise ConversionError("injected output preparation failure")

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.", suffix=".tmp", dir=parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
        except BaseException:
            # fdopen owns the descriptor after successful creation; this also
            # covers a write failure before the context manager can close it.
            raise

        if mode in {"replace", "replacement", "output-replacement"}:
            raise ConversionError("injected output replacement failure")
        os.replace(temporary_path, output_path)
        temporary_path = None
    except ConversionError:
        raise
    except OSError as exc:
        raise ConversionError(f"cannot publish output: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original, useful diagnostic. A best-effort
                # cleanup is preferable to masking the command failure.
                pass


def convert(input_path: Path, output_path: Path) -> None:
    parent = _validate_paths(input_path, output_path)
    rows = _read_tasks(input_path)
    payload = json.dumps(
        {"rows": rows}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    _write_transactionally(output_path, payload, parent)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="task_csv_to_json",
        description="Convert a validated task CSV to deterministic JSON.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    parser.add_argument("--input", required=True, dest="input_path")
    parser.add_argument("--output", required=True, dest="output_path")
    args = parser.parse_args(argv)

    try:
        convert(Path(args.input_path), Path(args.output_path))
    except (ConversionError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
