from __future__ import annotations


class RunnerError(Exception):
    """An expected error safe to serialize at the public CLI boundary."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
