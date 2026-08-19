#!/usr/bin/env python3
"""Diarize one or more media files with local acoustic speaker clustering.

The default ``auto`` engine tries an already usable pyannote Community-1
installation first and falls back to token-free SpeechBrain ECAPA clustering.
Source media is decoded to a temporary WAV and is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ECAPA_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"
SAMPLE_RATE = 16_000


@dataclass
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Audio or video files")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--asr-json",
        action="append",
        default=[],
        type=Path,
        help="Optional ASR JSON, repeated in the same order as inputs",
    )
    parser.add_argument("--num-speakers", type=int, help="Known exact speaker count")
    parser.add_argument("--min-speakers", type=int, default=2)
    parser.add_argument("--max-speakers", type=int, default=4)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--engine", choices=("auto", "ecapa", "pyannote"), default="auto"
    )
    parser.add_argument("--ecapa-model", default=ECAPA_MODEL)
    parser.add_argument("--ecapa-hf-endpoint", default="https://huggingface.co")
    parser.add_argument(
        "--allow-ecapa-hf-token",
        action="store_true",
        help="Allow inherited Hugging Face credentials for the ECAPA model fetch",
    )
    parser.add_argument("--pyannote-model", default=PYANNOTE_MODEL)
    parser.add_argument(
        "--pyannote-hf-endpoint",
        default="https://huggingface.co",
        help=(
            "Process-local Hugging Face endpoint used only while pyannote loads and runs; "
            "the inherited HF_ENDPOINT is restored afterward"
        ),
    )
    parser.add_argument(
        "--no-pyannote-fallback",
        action="store_true",
        help="Fail instead of falling back to ECAPA when pyannote cannot load",
    )
    parser.add_argument("--window-seconds", type=float, default=1.5)
    parser.add_argument("--hop-seconds", type=float, default=0.75)
    parser.add_argument("--min-window-seconds", type=float, default=0.75)
    parser.add_argument("--embedding-batch-size", type=int, default=16)
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--vad-min-speech-ms", type=int, default=180)
    parser.add_argument("--vad-min-silence-ms", type=int, default=180)
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing output JSON"
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.num_speakers is not None and args.num_speakers < 1:
        raise ValueError("--num-speakers must be at least 1")
    if args.min_speakers < 1 or args.max_speakers < args.min_speakers:
        raise ValueError("Require 1 <= --min-speakers <= --max-speakers")
    if args.window_seconds <= 0 or args.hop_seconds <= 0:
        raise ValueError("Window and hop durations must be positive")
    if not 0 < args.min_window_seconds <= args.window_seconds:
        raise ValueError("Require 0 < --min-window-seconds <= --window-seconds")
    if args.embedding_batch_size < 1:
        raise ValueError("--embedding-batch-size must be at least 1")
    if not 0 < args.vad_threshold < 1:
        raise ValueError("--vad-threshold must be between 0 and 1")
    if args.asr_json and len(args.asr_json) != len(args.inputs):
        raise ValueError("Repeat --asr-json exactly once per input, in input order")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_values(values: Iterable[float]) -> str:
    encoded = ",".join(f"{float(value):.8f}" for value in values).encode("ascii")
    return hashlib.sha256(encoded).hexdigest().upper()


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required but was not found on PATH")
    result = run_checked(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned an invalid duration for {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Invalid media duration for {path}: {duration!r}")
    return duration


def decode_wav(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found on PATH")
    run_checked(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            "-y",
            str(target),
        ]
    )


def read_pcm_wav(path: Path) -> Any:
    import numpy as np
    import torch

    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != SAMPLE_RATE:
            raise RuntimeError("Temporary WAV does not match 16 kHz mono contract")
        if handle.getsampwidth() != 2:
            raise RuntimeError("Temporary WAV is not signed 16-bit PCM")
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    return torch.from_numpy(samples.copy())


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def resolve_device(requested: str) -> str:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("torch is required for diarization") from exc
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "--device cuda was requested but torch.cuda.is_available() is false"
        )
    return requested


def sanitize_error(exc: BaseException) -> str:
    """Keep diagnostics useful without ever serializing an access token."""
    message = f"{type(exc).__name__}: {exc}"
    for env_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        secret = os.environ.get(env_name)
        if secret:
            message = message.replace(secret, "<redacted>")
    message = re.sub(r"hf_[A-Za-z0-9]{12,}", "<redacted-hf-token>", message)
    return message[:1200]


def merge_spans(spans: list[Span], max_gap: float = 0.12) -> list[Span]:
    merged: list[Span] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if span.duration <= 0:
            continue
        if merged and span.start <= merged[-1].end + max_gap:
            merged[-1].end = max(merged[-1].end, span.end)
        else:
            merged.append(Span(span.start, span.end))
    return merged


def run_silero_vad(audio: Any, args: argparse.Namespace) -> list[Span]:
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad
    except ImportError as exc:
        raise RuntimeError(
            "Install silero-vad to use the ECAPA diarization engine"
        ) from exc
    model = load_silero_vad(onnx=False)
    timestamps = get_speech_timestamps(
        audio,
        model,
        sampling_rate=SAMPLE_RATE,
        threshold=args.vad_threshold,
        min_speech_duration_ms=args.vad_min_speech_ms,
        min_silence_duration_ms=args.vad_min_silence_ms,
        return_seconds=False,
    )
    spans = [
        Span(float(item["start"]) / SAMPLE_RATE, float(item["end"]) / SAMPLE_RATE)
        for item in timestamps
    ]
    return merge_spans(spans)


def make_windows(
    spans: list[Span], args: argparse.Namespace
) -> tuple[list[Span], list[Span]]:
    windows: list[Span] = []
    too_short: list[Span] = []
    for span in spans:
        if span.duration < args.min_window_seconds:
            too_short.append(span)
            continue
        if span.duration <= args.window_seconds:
            windows.append(Span(span.start, span.end))
            continue
        starts: list[float] = []
        cursor = span.start
        last_start = span.end - args.window_seconds
        while cursor < last_start:
            starts.append(cursor)
            cursor += args.hop_seconds
        starts.append(last_start)
        deduplicated: list[float] = []
        for start in starts:
            if not deduplicated or start - deduplicated[-1] > 0.05:
                deduplicated.append(start)
        windows.extend(
            Span(start, min(span.end, start + args.window_seconds))
            for start in deduplicated
        )
    return windows, too_short


@contextmanager
def ecapa_hf_environment(endpoint: str, allow_token: bool) -> Any:
    """Isolate a public ECAPA fetch from stale inherited HF configuration."""
    keys = (
        "HF_ENDPOINT",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HUGGINGFACEHUB_API_TOKEN",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["HF_ENDPOINT"] = endpoint
    if not allow_token:
        os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def pyannote_hf_environment(endpoint: str) -> Any:
    """Temporarily select pyannote's endpoint without changing user settings."""
    previous = os.environ.get("HF_ENDPOINT")
    os.environ["HF_ENDPOINT"] = endpoint
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HF_ENDPOINT", None)
        else:
            os.environ["HF_ENDPOINT"] = previous


def load_ecapa(
    model_name: str, device: str, hf_endpoint: str, allow_hf_token: bool
) -> Any:
    try:
        with ecapa_hf_environment(hf_endpoint, allow_hf_token):
            from speechbrain.inference.speaker import EncoderClassifier

            return EncoderClassifier.from_hparams(
                source=model_name, run_opts={"device": device}
            )
    except Exception as exc:
        raise RuntimeError(
            "ECAPA model load failed. Verify matching torch/torchaudio, network access, "
            f"and endpoint {hf_endpoint!r}: {sanitize_error(exc)}"
        ) from exc


def embed_windows(
    audio: Any,
    windows: list[Span],
    classifier: Any,
    device: str,
    batch_size: int,
) -> Any:
    import numpy as np
    import torch

    embeddings: list[Any] = []
    for batch_start in range(0, len(windows), batch_size):
        batch_windows = windows[batch_start : batch_start + batch_size]
        clips = []
        for span in batch_windows:
            start = max(0, int(round(span.start * SAMPLE_RATE)))
            end = min(len(audio), int(round(span.end * SAMPLE_RATE)))
            clip = audio[start:end]
            if len(clip) < int(0.10 * SAMPLE_RATE):
                raise RuntimeError(f"Embedding window is unexpectedly short: {span}")
            clips.append(clip)
        padded = torch.nn.utils.rnn.pad_sequence(clips, batch_first=True)
        relative_lengths = torch.tensor(
            [len(clip) / padded.shape[1] for clip in clips],
            dtype=torch.float32,
            device=device,
        )
        with torch.inference_mode():
            batch_embeddings = classifier.encode_batch(
                padded.to(device), wav_lens=relative_lengths, normalize=True
            )
        vectors = (
            batch_embeddings.detach().float().cpu().numpy().reshape(len(clips), -1)
        )
        for span, vector in zip(batch_windows, vectors):
            norm = float(np.linalg.norm(vector))
            if not math.isfinite(norm) or norm <= 0:
                raise RuntimeError(f"ECAPA returned an invalid embedding for {span}")
            embeddings.append(vector / norm)
    return np.stack(embeddings)


def agglomerative_labels(embeddings: Any, count: int) -> Any:
    from sklearn.cluster import AgglomerativeClustering

    kwargs = {"n_clusters": count, "linkage": "average"}
    try:
        return AgglomerativeClustering(metric="cosine", **kwargs).fit_predict(
            embeddings
        )
    except TypeError:  # scikit-learn < 1.2
        return AgglomerativeClustering(affinity="cosine", **kwargs).fit_predict(
            embeddings
        )


def score_clusterings(
    embeddings: Any, args: argparse.Namespace
) -> tuple[Any, list[dict[str, Any]]]:
    import numpy as np
    from sklearn.metrics import silhouette_score

    sample_count = len(embeddings)
    requested = (
        [args.num_speakers]
        if args.num_speakers is not None
        else list(range(args.min_speakers, args.max_speakers + 1))
    )
    candidates: list[dict[str, Any]] = []
    best_labels: Any = None
    best_key: tuple[float, int] | None = None
    for count in requested:
        if count is None or count > sample_count:
            candidates.append(
                {
                    "speaker_count": count,
                    "silhouette": None,
                    "reason": f"requires at least {count} windows; found {sample_count}",
                    "selected": False,
                }
            )
            continue
        if count == 1:
            labels = np.zeros(sample_count, dtype=int)
            score = None
            reason = "silhouette is undefined for one cluster"
            key = (-1.0, -count)
        elif sample_count <= count:
            labels = agglomerative_labels(embeddings, count)
            score = None
            reason = "silhouette requires more samples than clusters"
            key = (-1.0, -count)
        else:
            labels = agglomerative_labels(embeddings, count)
            score = float(silhouette_score(embeddings, labels, metric="cosine"))
            reason = None
            # Prefer separation; use fewer speakers only as a deterministic tie-break.
            key = (score, -count)
        item = {
            "speaker_count": count,
            "silhouette": None if score is None else round(score, 6),
            "reason": reason,
            "selected": False,
        }
        candidates.append(item)
        if best_key is None or key > best_key:
            best_key = key
            best_labels = labels
    if best_labels is None:
        raise RuntimeError(
            f"No plausible cluster count for {sample_count} embedding windows; "
            "lower --num-speakers/--min-speakers or use longer audio"
        )
    selected_count = len(set(int(value) for value in best_labels))
    for item in candidates:
        if item["speaker_count"] == selected_count:
            item["selected"] = True
            break
    return best_labels, candidates


def relabel_by_first_appearance(labels: Any) -> list[int]:
    mapping: dict[int, int] = {}
    result: list[int] = []
    for raw in labels:
        value = int(raw)
        if value not in mapping:
            mapping[value] = len(mapping)
        result.append(mapping[value])
    return result


def smooth_labels(
    labels: list[int], embeddings: Any, windows: list[Span]
) -> tuple[list[int], list[dict[str, Any]]]:
    """Correct strong acoustic A-B-A outliers without swallowing unique short turns."""
    import numpy as np

    smoothed = labels[:]
    changes: list[dict[str, Any]] = []
    for _ in range(2):
        pending: list[tuple[int, int, int]] = []
        for index in range(1, len(smoothed) - 1):
            before = smoothed[index]
            after = smoothed[index - 1]
            if after != smoothed[index + 1] or after == before:
                continue
            # Do not erase a label supported by just one window: that may be a
            # real short answer. Do not smooth across separate speech regions.
            same_label_indexes = [
                i for i, label in enumerate(smoothed) if label == before and i != index
            ]
            if not same_label_indexes:
                continue
            if windows[index].start > windows[index - 1].end + 0.25:
                continue
            if windows[index + 1].start > windows[index].end + 0.25:
                continue
            neighbor_indexes = [
                i for i, label in enumerate(smoothed) if label == after and i != index
            ]
            own_centroid = np.mean(embeddings[same_label_indexes], axis=0)
            neighbor_centroid = np.mean(embeddings[neighbor_indexes], axis=0)
            vector = embeddings[index]
            own_distance = 1.0 - float(
                np.dot(vector, own_centroid) / (np.linalg.norm(own_centroid) + 1e-12)
            )
            neighbor_distance = 1.0 - float(
                np.dot(vector, neighbor_centroid)
                / (np.linalg.norm(neighbor_centroid) + 1e-12)
            )
            if neighbor_distance + 0.05 < own_distance:
                pending.append((index, before, after))
        if not pending:
            break
        for index, before, after in pending:
            smoothed[index] = after
            changes.append({"window_index": index, "from": before, "to": after})
    return smoothed, changes


def label_name(label: int) -> str:
    return f"SPEAKER_{label:02d}"


def windows_to_turns(
    speech_spans: list[Span], windows: list[Span], labels: list[int]
) -> tuple[list[dict[str, Any]], int]:
    """Partition each speech span at midpoints between embedding-window centers."""
    centers = [(window.start + window.end) / 2 for window in windows]
    pieces: list[tuple[float, float, int, str]] = []
    short_assigned = 0
    for speech in speech_spans:
        indexes = [
            index
            for index, center in enumerate(centers)
            if speech.start - 1e-6 <= center <= speech.end + 1e-6
        ]
        if not indexes:
            if not centers:
                continue
            nearest = min(
                range(len(centers)),
                key=lambda index: min(
                    abs(centers[index] - speech.start), abs(centers[index] - speech.end)
                ),
            )
            pieces.append(
                (
                    speech.start,
                    speech.end,
                    labels[nearest],
                    "nearest_window_for_short_speech",
                )
            )
            short_assigned += 1
            continue
        boundaries = [speech.start]
        boundaries.extend(
            (centers[left] + centers[right]) / 2
            for left, right in zip(indexes, indexes[1:])
        )
        boundaries.append(speech.end)
        for position, index in enumerate(indexes):
            pieces.append(
                (
                    boundaries[position],
                    boundaries[position + 1],
                    labels[index],
                    "embedding_window",
                )
            )

    turns: list[dict[str, Any]] = []
    for start, end, label, evidence in pieces:
        if end <= start:
            continue
        if (
            turns
            and turns[-1]["speaker"] == label_name(label)
            and start <= turns[-1]["end"] + 0.15
        ):
            turns[-1]["end"] = round(max(float(turns[-1]["end"]), end), 3)
            if evidence not in turns[-1]["evidence"]:
                turns[-1]["evidence"].append(evidence)
        else:
            turns.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "speaker": label_name(label),
                    "evidence": [evidence],
                }
            )
    return turns, short_assigned


def overlap_seconds(start: float, end: float, turn: dict[str, Any]) -> float:
    return max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"])))


def speaker_for_interval(
    start: float, end: float, turns: list[dict[str, Any]]
) -> str | None:
    if not turns:
        return None
    overlaps = [(overlap_seconds(start, end, turn), turn["speaker"]) for turn in turns]
    best_overlap, best_speaker = max(overlaps, key=lambda item: item[0])
    if best_overlap > 0:
        return str(best_speaker)
    midpoint = (start + end) / 2
    nearest = min(
        turns,
        key=lambda turn: min(
            abs(midpoint - float(turn["start"])),
            abs(midpoint - float(turn["end"])),
        ),
    )
    return str(nearest["speaker"])


def align_asr(path: Path, turns: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    aligned_segments: list[dict[str, Any]] = []
    aligned_words: list[dict[str, Any]] = []
    for segment in payload.get("segments", []):
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        aligned_segments.append(
            {
                "id": segment.get("id"),
                "start": start,
                "end": end,
                "speaker": speaker_for_interval(start, end, turns),
                "text": segment.get("text", ""),
            }
        )
        for word in segment.get("words") or []:
            if word.get("start") is None or word.get("end") is None:
                continue
            word_start = float(word["start"])
            word_end = float(word["end"])
            aligned_words.append(
                {
                    "start": word_start,
                    "end": word_end,
                    "speaker": speaker_for_interval(word_start, word_end, turns),
                    "word": word.get("word", ""),
                }
            )
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path.resolve()),
        "method": "maximum_time_overlap_then_nearest_turn",
        "segments": aligned_segments,
        "words": aligned_words,
    }


def diarize_ecapa(
    wav_path: Path, args: argparse.Namespace, device: str
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    import numpy as np

    audio = read_pcm_wav(wav_path)
    speech_spans = run_silero_vad(audio, args)
    if not speech_spans:
        raise RuntimeError("Silero VAD found no speech")
    windows, too_short = make_windows(speech_spans, args)
    if not windows:
        raise RuntimeError(
            "All detected speech regions are shorter than --min-window-seconds; "
            "lower that threshold only if the acoustic evidence is usable"
        )
    print(
        f"ECAPA: {len(speech_spans)} speech spans, {len(windows)} embedding windows; "
        f"loading {args.ecapa_model}",
        file=sys.stderr,
        flush=True,
    )
    classifier = load_ecapa(
        args.ecapa_model,
        device,
        args.ecapa_hf_endpoint,
        args.allow_ecapa_hf_token,
    )
    print(
        f"ECAPA: embedding on {device} in batches of {args.embedding_batch_size}",
        file=sys.stderr,
        flush=True,
    )
    embeddings = embed_windows(
        audio, windows, classifier, device, args.embedding_batch_size
    )
    raw_labels, candidates = score_clusterings(embeddings, args)
    labels = relabel_by_first_appearance(raw_labels)
    labels, smoothing = smooth_labels(labels, embeddings, windows)
    turns, short_assigned = windows_to_turns(speech_spans, windows, labels)

    window_evidence = []
    for index, (span, vector, label) in enumerate(zip(windows, embeddings, labels)):
        window_evidence.append(
            {
                "index": index,
                "start": round(span.start, 3),
                "end": round(span.end, 3),
                "speaker": label_name(label),
                "embedding_sha256": sha256_values(vector),
                "embedding": [round(float(value), 6) for value in vector],
            }
        )
    audit = {
        "speech_activity": {
            "method": "silero-vad",
            "segments": [
                {"start": round(span.start, 3), "end": round(span.end, 3)}
                for span in speech_spans
            ],
            "too_short_for_embedding": [
                {"start": round(span.start, 3), "end": round(span.end, 3)}
                for span in too_short
            ],
        },
        "embedding_windows": window_evidence,
        "cluster_candidates": candidates,
        "smoothing_changes": [
            {
                **change,
                "from": label_name(int(change["from"])),
                "to": label_name(int(change["to"])),
            }
            for change in smoothing
        ],
    }
    diagnostics = {
        "embedding_window_count": len(windows),
        "filtered_short_speech_count": len(too_short),
        "short_speech_assigned_by_nearest_window": short_assigned,
        "selected_speaker_count": len(set(labels)),
        "embedding_dimensions": int(np.asarray(embeddings).shape[1]),
    }
    return turns, audit, diagnostics


def annotation_turns(annotation: Any) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        turns.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "speaker": str(speaker),
                "evidence": ["pyannote_exclusive_diarization"],
            }
        )
    return sorted(turns, key=lambda item: (item["start"], item["end"]))


def diarize_pyannote(
    source: Path, args: argparse.Namespace, device: str
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    with pyannote_hf_environment(args.pyannote_hf_endpoint):
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError("pyannote.audio is not installed") from exc
        token = os.environ.get("HF_TOKEN") or os.environ.get(
            "HUGGING_FACE_HUB_TOKEN"
        )
        pipeline = Pipeline.from_pretrained(args.pyannote_model, token=token)
        if pipeline is None:
            raise RuntimeError(
                "pyannote pipeline could not be loaded; accept the model terms and configure HF_TOKEN"
            )
        pipeline.to(torch.device(device))
        kwargs: dict[str, int] = {}
        if args.num_speakers is not None:
            kwargs["num_speakers"] = args.num_speakers
        else:
            kwargs["min_speakers"] = args.min_speakers
            kwargs["max_speakers"] = args.max_speakers
        output = pipeline(str(source), **kwargs)
        annotation = getattr(output, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(output, "speaker_diarization", output)
        turns = annotation_turns(annotation)
        if not turns:
            raise RuntimeError("pyannote returned no speaker turns")
    audit = {
        "speech_activity": {"method": "pyannote-community-1", "segments": []},
        "embedding_windows": [],
        "cluster_candidates": [
            {
                "speaker_count": len({turn["speaker"] for turn in turns}),
                "silhouette": None,
                "reason": "pyannote pipeline does not expose comparable window embeddings here",
                "selected": True,
            }
        ],
        "smoothing_changes": [],
        "raw_exclusive_turns": turns,
    }
    diagnostics = {
        "selected_speaker_count": len({turn["speaker"] for turn in turns}),
        "embedding_window_count": None,
        "filtered_short_speech_count": None,
        "short_speech_assigned_by_nearest_window": None,
    }
    return turns, audit, diagnostics


def output_path(output_dir: Path, source: Path, duplicate_stems: set[str]) -> Path:
    suffix = (
        f".{sha256_file(source)[:8]}"
        if source.stem.casefold() in duplicate_stems
        else ""
    )
    return output_dir / f"{source.stem}{suffix}.diarization.json"


def coverage(turns: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    spans = merge_spans(
        [Span(float(turn["start"]), float(turn["end"])) for turn in turns], max_gap=0
    )
    covered = sum(span.duration for span in spans)
    return {
        "media_duration_seconds": round(duration, 3),
        "diarized_speech_seconds": round(covered, 3),
        "diarized_fraction_of_media": (
            round(covered / duration, 6) if duration else None
        ),
        "turn_count": len(turns),
        "last_turn_end_seconds": round(
            max((float(turn["end"]) for turn in turns), default=0), 3
        ),
    }


def dependency_versions() -> dict[str, str | None]:
    return {
        name: package_version(name)
        for name in (
            "torch",
            "torchaudio",
            "speechbrain",
            "silero-vad",
            "scikit-learn",
            "pyannote.audio",
        )
    }


def process_one(
    source: Path,
    asr_json: Path | None,
    target: Path,
    args: argparse.Namespace,
    device: str,
) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists() and not args.force:
        raise FileExistsError(
            f"Refusing to overwrite {target}; pass --force if intentional"
        )
    source_hash_before = sha256_file(source)
    duration = probe_duration(source)
    fallback_reason: str | None = None
    selected_engine = args.engine
    with tempfile.TemporaryDirectory(prefix="codex-diarization-") as temp_name:
        wav_path = Path(temp_name) / "audio.wav"
        decode_wav(source, wav_path)
        if args.engine in ("auto", "pyannote"):
            try:
                turns, audit, diagnostics = diarize_pyannote(source, args, device)
                selected_engine = "pyannote"
            except Exception as exc:
                fallback_reason = sanitize_error(exc)
                if args.engine == "pyannote" and args.no_pyannote_fallback:
                    raise RuntimeError(f"pyannote failed: {fallback_reason}") from exc
                turns, audit, diagnostics = diarize_ecapa(wav_path, args, device)
                selected_engine = "ecapa"
        else:
            turns, audit, diagnostics = diarize_ecapa(wav_path, args, device)
            selected_engine = "ecapa"

    source_hash_after = sha256_file(source)
    if source_hash_after != source_hash_before:
        raise RuntimeError(f"Source media changed during processing: {source}")
    selected_model = (
        args.pyannote_model if selected_engine == "pyannote" else args.ecapa_model
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source),
            "size_bytes": source.stat().st_size,
            "sha256_before": source_hash_before,
            "sha256_after": source_hash_after,
            "unchanged": True,
            "duration_seconds": round(duration, 6),
        },
        "engine": {
            "requested": args.engine,
            "selected": selected_engine,
            "model": selected_model,
            "device": device,
            "pyannote_fallback_reason": fallback_reason,
            "pyannote_hf_endpoint_used": (
                args.pyannote_hf_endpoint
                if args.engine in ("auto", "pyannote")
                else None
            ),
            "dependency_versions": dependency_versions(),
        },
        "settings": {
            "num_speakers": args.num_speakers,
            "min_speakers": args.min_speakers,
            "max_speakers": args.max_speakers,
            "sample_rate": SAMPLE_RATE,
            "window_seconds": args.window_seconds,
            "hop_seconds": args.hop_seconds,
            "min_window_seconds": args.min_window_seconds,
            "embedding_batch_size": args.embedding_batch_size,
            "ecapa_hf_endpoint": args.ecapa_hf_endpoint,
            "ecapa_inherited_hf_token_allowed": args.allow_ecapa_hf_token,
            "pyannote_hf_endpoint": args.pyannote_hf_endpoint,
            "vad_threshold": args.vad_threshold,
            "vad_min_speech_ms": args.vad_min_speech_ms,
            "vad_min_silence_ms": args.vad_min_silence_ms,
        },
        "label_contract": {
            "type": "anonymous_acoustic_cluster",
            "warning": "SPEAKER_XX labels are not identities or semantic roles",
        },
        "turns": turns,
        "coverage": coverage(turns, duration),
        "audit": audit,
        "diagnostics": diagnostics,
    }
    if asr_json is not None:
        payload["asr_alignment"] = align_asr(asr_json.resolve(), turns)
    return payload


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        device = resolve_device(args.device)
        sources = [path.resolve() for path in args.inputs]
        stem_counts: dict[str, int] = {}
        for source in sources:
            key = source.stem.casefold()
            stem_counts[key] = stem_counts.get(key, 0) + 1
        duplicate_stems = {stem for stem, count in stem_counts.items() if count > 1}
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(sources):
            target = output_path(output_dir, source, duplicate_stems)
            asr_json = args.asr_json[index] if args.asr_json else None
            print(
                f"[{index + 1}/{len(sources)}] diarizing {source}",
                file=sys.stderr,
                flush=True,
            )
            payload = process_one(source, asr_json, target, args, device)
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(target)
        return 0
    except Exception as exc:
        print(f"ERROR: {sanitize_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
