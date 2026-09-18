"""Canonical task identity and recovery-index title tokens for implement-needs.

The authoritative identity of a managed task is the task backend record (formal
thread id plus host id). A title token is only a recovery index: it lets a
restarted controller find candidate tasks after a lost ``clientThreadId``. It is
never accepted as proof of identity on its own.

Title layout, version 1 (single space separators, fixed field order)::

    [INN v=1 task=<id> run=<id> attempt=<NN> nonce=<16 hex>] <description>

The description is free text and is never parsed. ``nonce`` exists only to
disambiguate attempts created after a local backup restore; it is not
authentication and never gates identity by itself.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

TITLE_TOKEN_VERSION = 1
NONCE_BYTES = 8
NONCE_HEX_LENGTH = NONCE_BYTES * 2
MAX_DESCRIPTION_LENGTH = 400

_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9._:-]*"
_ATTEMPT = r"[0-9]{2,}"
_NONCE = rf"[0-9a-f]{{{NONCE_HEX_LENGTH}}}"

TITLE_RE = re.compile(
    rf"^\[INN v=(?P<version>[0-9]+)"
    rf" task=(?P<task_id>{_IDENTIFIER})"
    rf" run=(?P<run_id>{_IDENTIFIER})"
    rf" attempt=(?P<attempt_id>{_ATTEMPT})"
    rf" nonce=(?P<nonce>{_NONCE})\](?: (?P<description>.*))?$"
)

#: Reject any title whose token region contains characters outside the grammar.
_FORBIDDEN = re.compile(r"[\[\]\s]")

MISSING = "title_token_missing"
MALFORMED = "title_token_malformed"
UNSUPPORTED_VERSION = "title_token_unsupported_version"
EMPTY_DESCRIPTION = "title_token_empty_description"
DESCRIPTION_TOO_LONG = "title_token_description_too_long"
INVALID_ATTEMPT = "title_token_invalid_attempt"


@dataclass(frozen=True)
class TaskIdentity:
    """The identity triple that a managed task must keep stable for its life."""

    task_id: str
    run_id: str
    attempt_id: str
    nonce: str

    def as_fields(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "nonce": self.nonce,
        }


@dataclass(frozen=True)
class ParsedTitle:
    identity: TaskIdentity | None
    description: str | None
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.identity is not None and not self.errors


def mint_nonce() -> str:
    """Return a fresh collision-detection nonce."""
    return secrets.token_hex(NONCE_BYTES)


def next_attempt_id(current: str | None) -> str:
    """Return the next attempt id, always zero padded to at least two digits.

    Callers must only advance an attempt after the previous attempt reached a
    terminal outcome; advancing while an attempt is live is what produces
    duplicate tasks.
    """
    if current is None:
        return "01"
    if not re.fullmatch(_ATTEMPT, current):
        raise ValueError(f"invalid attempt id: {current!r}")
    return f"{int(current) + 1:02d}"


def format_title(identity: TaskIdentity, description: str) -> str:
    """Return the canonical title for ``identity`` and ``description``."""
    return f"{format_token(identity)} {validate_description(description)}"


def format_token(identity: TaskIdentity) -> str:
    """Return only the machine-readable token prefix."""
    _validate_identity(identity)
    return (
        f"[INN v={TITLE_TOKEN_VERSION}"
        f" task={identity.task_id}"
        f" run={identity.run_id}"
        f" attempt={identity.attempt_id}"
        f" nonce={identity.nonce}]"
    )


def validate_description(description: str) -> str:
    if not isinstance(description, str):
        raise TypeError("description must be a string")
    stripped = description.strip()
    if not stripped:
        raise ValueError("description must not be empty")
    if len(stripped) > MAX_DESCRIPTION_LENGTH:
        raise ValueError("description is too long")
    # Newlines collapse to spaces so the token stays on one title line.
    return " ".join(stripped.split())


def _validate_identity(identity: TaskIdentity) -> None:
    if not re.fullmatch(_IDENTIFIER, identity.task_id or ""):
        raise ValueError(f"invalid task_id: {identity.task_id!r}")
    if not re.fullmatch(_IDENTIFIER, identity.run_id or ""):
        raise ValueError(f"invalid run_id: {identity.run_id!r}")
    if not re.fullmatch(_ATTEMPT, identity.attempt_id or ""):
        raise ValueError(f"invalid attempt_id: {identity.attempt_id!r}")
    if not re.fullmatch(_NONCE, identity.nonce or ""):
        raise ValueError(f"invalid nonce: {identity.nonce!r}")


def parse_title(title: object) -> ParsedTitle:
    """Parse a title into its identity and description, collecting every defect.

    Returns an identity with ``ok`` true only when the token is present, well
    formed, supported, and carries a usable description.
    """
    if not isinstance(title, str) or not title:
        return ParsedTitle(None, None, (MISSING,))
    stripped = title.strip()
    if not stripped.startswith("[INN"):
        return ParsedTitle(None, None, (MISSING,))
    match = TITLE_RE.match(stripped)
    if match is None:
        return ParsedTitle(None, None, (_classify_malformed(stripped),))
    version = int(match.group("version"))
    if version != TITLE_TOKEN_VERSION:
        return ParsedTitle(None, None, (UNSUPPORTED_VERSION,))
    identity = TaskIdentity(
        task_id=match.group("task_id"),
        run_id=match.group("run_id"),
        attempt_id=match.group("attempt_id"),
        nonce=match.group("nonce"),
    )
    description = match.group("description")
    errors: list[str] = []
    if description is None or not description.strip():
        errors.append(EMPTY_DESCRIPTION)
        description = None
    elif len(description.strip()) > MAX_DESCRIPTION_LENGTH:
        errors.append(DESCRIPTION_TOO_LONG)
    else:
        description = description.strip()
    return ParsedTitle(identity, description, tuple(errors))


def _classify_malformed(stripped: str) -> str:
    """Separate a wrong-shaped token from a truncated or edited one.

    A token that never closes is truncated, so there is nothing further to
    inspect. An attempt field that is present but wrongly shaped is reported
    precisely, because that is the one defect a human is likely to have edited.
    """
    if "]" not in stripped:
        return MALFORMED
    body = stripped.split("]", 1)[0]
    tokens = dict(re.findall(r"(\w+)=([^\s\]]+)", body))
    if "attempt" in tokens and not re.fullmatch(_ATTEMPT, tokens["attempt"]):
        return INVALID_ATTEMPT
    return MALFORMED


def token_matches(identity: TaskIdentity, parsed: ParsedTitle) -> bool:
    """True when a parsed title carries exactly this identity triple."""
    return (
        parsed.identity is not None
        and parsed.identity.as_fields() == identity.as_fields()
    )


def resolve_candidates(identity: TaskIdentity, titles: list[str]) -> tuple[list[int], list[str]]:
    """Return matching indexes plus defects for candidate recovery decisions.

    Identity binding requires exactly one match. Zero matches means the task
    cannot be recovered from titles; more than one means the registry is in an
    ambiguous state and no automatic action may be taken.
    """
    matches: list[int] = []
    mismatches: list[str] = []
    for index, title in enumerate(titles):
        parsed = parse_title(title)
        if parsed.identity is None:
            mismatches.append(parsed.errors[0] if parsed.errors else MISSING)
            continue
        if token_matches(identity, parsed):
            matches.append(index)
        else:
            mismatches.append("identity_mismatch")
    return matches, mismatches
