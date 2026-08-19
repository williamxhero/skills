#!/usr/bin/env python3
"""Transcribe media locally with faster-whisper and preserve audit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Audio or video files")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="Systran/faster-whisper-medium")
    parser.add_argument("--language", default="auto", help="Language code or auto")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--start", type=float, default=0.0, help="Start seconds")
    parser.add_argument("--end", type=float, help="Exclusive end seconds")
    parser.add_argument("--initial-prompt")
    parser.add_argument(
        "--condition-on-previous-text",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing JSON")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


def choose_device_and_compute(device: str, compute_type: str) -> tuple[str, str]:
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def clip_media(source: Path, start: float, end: float | None, temp_dir: Path) -> Path:
    if start <= 0 and end is None:
        return source
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for --start or --end recovery passes")
    if end is not None and end <= start:
        raise ValueError("--end must be greater than --start")
    clipped = temp_dir / f"{source.stem}.clip.wav"
    command = [ffmpeg, "-v", "error", "-ss", str(start), "-i", str(source)]
    if end is not None:
        command.extend(["-t", str(end - start)])
    command.extend(["-vn", "-ac", "1", "-ar", "16000", "-y", str(clipped)])
    subprocess.run(command, check=True)
    return clipped


def word_to_dict(word: Any, offset: float) -> dict[str, Any]:
    return {
        "start": None if word.start is None else round(float(word.start) + offset, 3),
        "end": None if word.end is None else round(float(word.end) + offset, 3),
        "word": word.word,
        "probability": None if word.probability is None else float(word.probability),
    }


def transcribe_one(args: argparse.Namespace, model: Any, source: Path) -> Path:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output = args.output_dir.resolve() / f"{source.stem}.raw.json"
    if output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite {output}; pass --force if intentional")

    duration = probe_duration(source)
    if duration is not None and args.start >= duration:
        raise ValueError(f"--start {args.start} is beyond media duration {duration:.3f}")
    effective_end = min(args.end, duration) if args.end is not None and duration else args.end

    with tempfile.TemporaryDirectory(prefix="codex-asr-") as temp_name:
        media = clip_media(source, args.start, effective_end, Path(temp_name))
        segments_iter, info = model.transcribe(
            str(media),
            language=None if args.language == "auto" else args.language,
            beam_size=args.beam_size,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=args.condition_on_previous_text,
            initial_prompt=args.initial_prompt,
        )
        segments = []
        for segment in segments_iter:
            segments.append(
                {
                    "id": segment.id,
                    "start": round(float(segment.start) + args.start, 3),
                    "end": round(float(segment.end) + args.start, 3),
                    "text": segment.text,
                    "avg_logprob": float(segment.avg_logprob),
                    "compression_ratio": float(segment.compression_ratio),
                    "no_speech_prob": float(segment.no_speech_prob),
                    "words": [word_to_dict(word, args.start) for word in (segment.words or [])],
                }
            )

    payload = {
        "schema_version": 1,
        "source": {
            "path": str(source),
            "size_bytes": source.stat().st_size,
            "sha256": sha256(source),
            "duration_seconds": duration,
        },
        "requested_range": {"start": args.start, "end": effective_end},
        "settings": {
            "model": args.model,
            "device": args.device_resolved,
            "compute_type": args.compute_resolved,
            "language": args.language,
            "beam_size": args.beam_size,
            "temperature": 0.0,
            "vad_filter": True,
            "word_timestamps": True,
            "condition_on_previous_text": args.condition_on_previous_text,
            "initial_prompt": args.initial_prompt,
        },
        "detected_language": info.language,
        "language_probability": float(info.language_probability),
        "duration_after_vad": float(info.duration_after_vad),
        "segments": segments,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    args = parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed", file=sys.stderr)
        return 2

    args.device_resolved, args.compute_resolved = choose_device_and_compute(
        args.device, args.compute_type
    )
    try:
        model = WhisperModel(
            args.model,
            device=args.device_resolved,
            compute_type=args.compute_resolved,
        )
        for source in args.inputs:
            output = transcribe_one(args, model, source)
            print(output)
    except Exception as exc:
        print(f"ASR failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
