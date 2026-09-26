from __future__ import annotations

import math
from collections.abc import Sequence

from .errors import RunnerError


_SENSITIVE_OPTIONS = frozenset({"-f", "-F", "--field", "--raw-field", "-H", "--header"})


def validate_github_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerError("github_timeout_invalid", "GitHub timeout must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RunnerError("github_timeout_invalid", "GitHub timeout must be a positive finite number")
    return result


def safe_github_args(arguments: Sequence[str]) -> list[str]:
    """Retain command identity while excluding field and header values."""
    safe: list[str] = []
    redact_next = False
    for argument in arguments:
        if redact_next:
            safe.append("[redacted]")
            redact_next = False
        elif argument in _SENSITIVE_OPTIONS:
            safe.append(argument)
            redact_next = True
        elif any(argument.startswith(f"{option}=") for option in _SENSITIVE_OPTIONS):
            safe.append(argument.split("=", 1)[0] + "=[redacted]")
        else:
            safe.append(argument)
    return safe
