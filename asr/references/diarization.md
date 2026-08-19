# Acoustic speaker diarization

Use diarization to answer "which acoustic voice spoke when." Do not treat it as identity recognition or assign semantic role names from acoustics alone.

## Install the isolated environment

Use Python 3.11 or 3.12. Install a matching `torch`/`torchaudio` pair for the chosen CUDA runtime, then install:

```powershell
python -m pip install speechbrain silero-vad scikit-learn numpy
```

The ECAPA path uses Silero's PyTorch backend and does not require `onnxruntime` or a Hugging Face token. It downloads the public `speechbrain/spkrec-ecapa-voxceleb` model on first use. By default the script temporarily suppresses inherited Hugging Face tokens and uses `https://huggingface.co`, preventing a stale token or unrelated `HF_ENDPOINT` mirror from breaking the public fetch; it restores the process environment after model loading. Use `--allow-ecapa-hf-token` or `--ecapa-hf-endpoint` only when intentionally required. Keep `ffmpeg` and `ffprobe` on `PATH`.

Install `pyannote.audio` separately only when Community-1 is wanted. Accept that model's Hugging Face terms and provide the token through `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN`; never put a token in a command, script, JSON, or transcript.

The pyannote path temporarily sets process-local `HF_ENDPOINT=https://huggingface.co` before importing pyannote and keeps it in force through model loading and inference. It restores the inherited value after success, failure, or ECAPA fallback; it never changes User or Machine environment variables. This lets a global `HF_ENDPOINT=https://hf-mirror.com` remain available for unrelated downloads. Override the one-call pyannote endpoint only when intentionally required:

```powershell
python scripts/diarize_speakers.py "meeting.m4a" `
  --output-dir "asr-output" --engine pyannote `
  --pyannote-hf-endpoint "https://huggingface.co"
```

The selected pyannote endpoint is non-secret and is recorded in the output JSON. Tokens are inherited in memory for authorized model access but are never emitted or rewritten.

## Select the engine

`--engine auto` prefers pyannote Community-1 and falls back to ECAPA when pyannote is missing, unauthorized, or fails. The output records a sanitized fallback reason. Use `--engine ecapa` for a deterministic token-free path. Use `--engine pyannote --no-pyannote-fallback` when pyannote is mandatory and failure must stop the task.

When the speaker count is known, fix it:

```powershell
python scripts/diarize_speakers.py "one.m4a" "two.m4a" `
  --output-dir "asr-output" --engine ecapa --num-speakers 2 --device cuda
```

When it is unknown, compare a bounded plausible range:

```powershell
python scripts/diarize_speakers.py "meeting.m4a" `
  --output-dir "asr-output" --min-speakers 2 --max-speakers 5
```

Align existing faster-whisper evidence by repeating `--asr-json` in the same order as inputs:

```powershell
python scripts/diarize_speakers.py "one.m4a" "two.m4a" `
  --output-dir "asr-output" --engine ecapa --num-speakers 2 `
  --asr-json "asr-output/one.raw.json" --asr-json "asr-output/two.raw.json"
```

The script refuses to overwrite output unless `--force` is explicit. It decodes only to a temporary WAV, hashes the source before and after processing, and never modifies source media.

## Read the evidence

Each `*.diarization.json` contains:

- anonymous timestamped `SPEAKER_XX` turns;
- source duration and before/after SHA-256;
- selected model, engine, device, settings, and dependency versions;
- Silero speech spans, filtered short spans, window embeddings and labels;
- every plausible cluster count with silhouette, or `null` plus the reason it is undefined;
- isolated-label smoothing changes, short-speech nearest-window assignments, coverage, and diagnostics;
- optional ASR word and segment alignment.

Silhouette compares separation only within this recording; it does not prove the chosen count or identity. Very short acknowledgements are filtered from embedding extraction and assigned from the nearest usable acoustic window, so mark those assignments as lower confidence. Overlap, background speech, a voice moving away from the microphone, and strong channel changes can cause label errors.

## Map acoustic labels to roles

Perform role mapping as a second pass:

1. Inspect stable, longer `SPEAKER_XX` turns and their aligned text.
2. Use conversational evidence to propose role mappings such as `SPEAKER_00 -> 引导者`.
3. Check openings, closings, question-answer transitions, short acknowledgements, and suspected label drift.
4. Keep `SPEAKER_XX` or mark the role uncertain when evidence conflicts.
5. Never infer a real name, family relation, age, or identity without explicit evidence.
