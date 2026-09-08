#!/usr/bin/env python3
"""Validate a Markdown export produced by the download-chat skill."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROLE_RE = re.compile(r"^## (User|ChatGPT)$", re.MULTILINE)
UI_ONLY_LINES = {
    "Copy response",
    "Rate response",
    "Share",
    "Switch model",
    "More actions",
    "ChatGPT can make mistakes. Check important info.",
}


def validate_text(
    text: str, expected_title: str | None = None, min_turns: int = 1
) -> list[str]:
    errors: list[str] = []
    if "\x00" in text:
        errors.append("contains NUL bytes")

    lines = text.splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if not first.startswith("# ") or first.startswith("## "):
        errors.append("first non-empty line must be one H1 title")
    elif expected_title is not None and first[2:].strip() != expected_title.strip():
        errors.append(
            f"title mismatch: expected {expected_title!r}, got {first[2:].strip()!r}"
        )

    roles = ROLE_RE.findall(text)
    if len(roles) < min_turns:
        errors.append(f"expected at least {min_turns} turns, found {len(roles)}")

    stripped_lines = {line.strip() for line in lines}
    leaked = sorted(UI_ONLY_LINES & stripped_lines)
    if leaked:
        errors.append("contains ChatGPT UI-only lines: " + ", ".join(leaked))

    if roles:
        last_role_match = list(ROLE_RE.finditer(text))[-1]
        trailing = text[last_role_match.end() :].strip()
        if len(trailing) < 2:
            errors.append("last turn has no substantive body")

    return errors


def self_test() -> int:
    valid = "# 示例聊天\n\n## User\n\n你好\n\n## ChatGPT\n\n你好！\n"
    invalid = "# 错误标题\n\n## User\n\n"
    assert not validate_text(valid, "示例聊天", 2)
    assert validate_text(invalid, "示例聊天", 2)
    print("self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--expected-title")
    parser.add_argument("--min-turns", type=int, default=1)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.path is None:
        parser.error("path is required unless --self-test is used")
    if args.min_turns < 1:
        parser.error("--min-turns must be at least 1")
    if args.path.suffix.lower() != ".md":
        print("validation failed: output path must end in .md", file=sys.stderr)
        return 1

    try:
        raw = args.path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 1

    errors = validate_text(text, args.expected_title, args.min_turns)
    if errors:
        for error in errors:
            print(f"validation failed: {error}", file=sys.stderr)
        return 1

    print(
        f"validated: {args.path} ({len(raw)} bytes, "
        f"{len(ROLE_RE.findall(text))} turns, UTF-8)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

