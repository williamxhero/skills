"""Compatibility entry point for the task CSV parser."""

from task_csv_to_json import (
    REQUIRED_COLUMNS,
    ValidationError,
    parse,
    parse_csv,
    parse_file,
    parse_tasks,
    read_tasks,
)

__all__ = [
    "REQUIRED_COLUMNS",
    "ValidationError",
    "parse",
    "parse_csv",
    "parse_file",
    "parse_tasks",
    "read_tasks",
]
