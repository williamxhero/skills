#!/usr/bin/env python3
"""Audit JSON produced by transcribe_faster_whisper.py for ASR failure modes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_files", nargs="+", type=Path)
    parser.add_argument("--strict", action="store_true", help="Exit 2 when anomalies exist")
    parser.add_argument("--compression-threshold", type=float, default=2.4)
    parser.add_argument("--repeat-run", type=int, default=6)
    parser.add_argument("--gap-seconds", type=float, default=30.0)
    return parser.parse_args()


def normalize_word(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value, flags=re.UNICODE).lower()


def max_run(words: list[str]) -> tuple[str, int]:
    best_word = ""
    best_count = 0
    current_word = ""
    current_count = 0
    for word in words:
        if word and word == current_word:
            current_count += 1
        else:
            current_word = word
            current_count = 1 if word else 0
        if current_count > best_count:
            best_word, best_count = current_word, current_count
    return best_word, best_count


def audit(path: Path, args: argparse.Namespace) -> tuple[list[str], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments: list[dict[str, Any]] = payload.get("segments", [])
    findings: list[str] = []
    notes: list[str] = []
    if "\ufffd" in path.read_text(encoding="utf-8"):
        findings.append("contains U+FFFD replacement characters")
    if not segments:
        findings.append("contains no speech segments")
        return findings, notes

    previous_end: float | None = None
    all_words: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        seg_id = segment.get("id", "?")
        if end < start:
            findings.append(f"segment {seg_id} has end before start")
        if previous_end is not None:
            if start < previous_end - 0.05:
                findings.append(f"segment {seg_id} overlaps or moves backward")
            gap = start - previous_end
            if gap >= args.gap_seconds:
                notes.append(f"silence/gap {gap:.1f}s before segment {seg_id}; verify against audio")
        previous_end = max(previous_end or end, end)

        compression = float(segment.get("compression_ratio", 0.0))
        if compression >= args.compression_threshold:
            findings.append(f"segment {seg_id} high compression_ratio={compression:.2f}")
        words = segment.get("words") or []
        all_words.extend(words)
        normalized = [normalize_word(str(word.get("word", ""))) for word in words]
        repeated_word, repeated_count = max_run(normalized)
        if repeated_count >= args.repeat_run:
            findings.append(
                f"segment {seg_id} repeats {repeated_word!r} consecutively {repeated_count} times"
            )
        nonempty = [word for word in normalized if word]
        if len(nonempty) >= 12:
            word, count = Counter(nonempty).most_common(1)[0]
            if count / len(nonempty) >= 0.60:
                findings.append(
                    f"segment {seg_id} dominated by {word!r}: {count}/{len(nonempty)} words"
                )

    timestamp_counts: Counter[tuple[float, float]] = Counter()
    for word in all_words:
        if word.get("start") is None or word.get("end") is None:
            continue
        start = round(float(word["start"]), 3)
        end = round(float(word["end"]), 3)
        if end <= start:
            timestamp_counts[(start, end)] += 1
    for (start, end), count in timestamp_counts.items():
        if count >= 3:
            findings.append(f"{count} zero-duration words share timestamp {start:.3f}-{end:.3f}")

    source = payload.get("source", {})
    requested = payload.get("requested_range", {})
    target_end = requested.get("end") or source.get("duration_seconds")
    transcript_end = float(segments[-1].get("end", 0.0))
    if target_end is not None:
        remainder = float(target_end) - transcript_end
        notes.append(
            f"coverage ends at {transcript_end:.3f}s of target {float(target_end):.3f}s "
            f"({max(0.0, remainder):.3f}s remainder)"
        )
    return findings, notes


def main() -> int:
    args = parse_args()
    anomaly_count = 0
    for path in args.json_files:
        print(f"== {path} ==")
        try:
            findings, notes = audit(path, args)
        except Exception as exc:
            print(f"ERROR: {exc}")
            anomaly_count += 1
            continue
        if findings:
            anomaly_count += len(findings)
            for finding in findings:
                print(f"ANOMALY: {finding}")
        else:
            print("ANOMALY: none detected")
        for note in notes:
            print(f"NOTE: {note}")
    if args.strict and anomaly_count:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
