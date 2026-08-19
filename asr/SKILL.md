---
name: asr
description: Transcribe and analyze speech recordings with local-first ASR, acoustic speaker diarization or dialogue-role attribution, context-aware proofreading, homophone correction, repetition compression, and intent summaries. Use when Codex must process audio or video speech such as .m4a, .mp3, .wav, .flac, .aac, .mp4, meetings, courses, interviews, calls, voice notes, or meditation recordings; cluster or identify speakers or roles; create timestamped corrected transcripts; or diagnose suspicious ASR output.
---

# ASR

Produce a trustworthy, readable transcript rather than treating one decoder pass as ground truth. Prefer local inference, preserve the source, recover decoder failures, and separate what was spoken from what is factually established.

## Working contract

- Process every in-scope recording unless the user narrows the set.
- Never overwrite, rename, convert in place, or otherwise modify source media.
- Put generated artifacts in a clearly named output directory owned by the target project.
- Prefer local ASR and local reasoning. Call an external LLM only when it is necessary and authorized.
- Mark uncertainty explicitly. Never invent missing speech, names, roles, or exact repetition counts.
- Deliver corrected, speaker-attributed Markdown plus a compact index for multiple recordings.

## Workflow

### 1. Inventory and protect the sources

1. Resolve the real input path before doing long work. Search nearby drives only when the requested relative path is absent.
2. Enumerate supported media recursively and record file size, codec, sample rate, channels, and duration with `ffprobe` when available.
3. Record a SHA-256 hash for each original before processing. Recompute it before handoff.
4. Mirror the source-relative subdirectory structure under the output directory so recordings with the same filename stem cannot collide.
5. Read project instructions and preserve unrelated files. If the media belongs to another independent project, obey the active project-ownership/delegation rules.
6. Probe ASR, GPU, `ffmpeg`, available disk space, and any authorized remote service before starting a long batch. Stop and report an unavailable required interface instead of silently changing scope.

### 2. Choose the least external processing path

Use this order:

1. A locally cached, accurate `faster-whisper` model on GPU.
2. Another installed local ASR engine when it is demonstrably better suited.
3. A remote ASR or LLM only when local processing cannot meet the request and the user authorizes it.

Select the best practical cached model for the hardware. For Chinese conversational audio, `faster-whisper-medium`, beam size 5, VAD, and word timestamps are a strong starting point. Record the exact model and settings in the deliverable.

If the user authorizes the personal CPA fallback, probe `http://yosef-server:8317/` first and use the requested model, commonly `gpt-5.6-luna`. Do not expose credentials. Use it only for bounded ambiguous passages or semantic adjudication, not as the default transcription engine.

### 3. Generate a timestamped local draft

Use `scripts/transcribe_faster_whisper.py` for a repeatable first pass:

```powershell
python scripts/transcribe_faster_whisper.py "recording.m4a" --output-dir "asr-output" --model "Systran/faster-whisper-medium" --language zh
```

The script preserves word timestamps, confidence values, decoder diagnostics, the requested time range, and the source hash. Use `--start` and `--end` for bounded recovery passes. Use `--no-condition-on-previous-text` when a prior phrase may be locking the decoder.

For batches, invoke the script per source directory or provide a distinct mirrored output directory for each source-relative directory. It intentionally refuses to overwrite an existing JSON unless `--force` is explicitly passed.

Do not publish the raw JSON as the final transcript. It is evidence for correction and auditing.

### 4. Audit before proofreading

Run:

```powershell
python scripts/audit_asr_json.py "asr-output/recording.raw.json"
```

Investigate, rather than automatically delete, any of these:

- very high compression ratio;
- long runs of one word or phrase;
- many words sharing one zero-length timestamp;
- transcript coverage suddenly ending before the audio;
- an implausible topic or vocabulary shift;
- long silence gaps that conflict with audible speech;
- replacement characters or malformed text.

Repetitive source material, such as a mantra or drill, can legitimately look anomalous. Always spot-check the audio or run independent windows before deciding it is hallucination.

### 5. Recover context lock and decoder loops

Treat a long plausible-looking repetition as a possible decoder failure when it begins abruptly and suppresses later topic changes.

1. Decode several short windows before, inside, and after the suspicious range independently.
2. If those windows disagree with the continuous pass, re-decode from slightly before the failure to the end with `condition_on_previous_text=false`.
3. Merge at a natural silence or sentence boundary and keep the original timestamps.
4. Remove only confirmed decoder artifacts, such as dozens of words at exactly the same timestamp or a loop absent from independent audio checks.
5. Record the recovery range and method in the final quality notes.

### 6. Identify speakers as roles

- Use `scripts/diarize_speakers.py` when speaker attribution matters. Read `references/diarization.md` before installing dependencies, choosing an engine, or interpreting its JSON.
- Prefer an installed and authorized pyannote Community-1 pipeline. If its package, model access, or token is unavailable, use the local SpeechBrain ECAPA + Silero VAD fallback; record the sanitized fallback reason.
- Keep the user's global Hugging Face mirror unchanged. The script temporarily uses the official endpoint for pyannote only, then restores the inherited `HF_ENDPOINT` after success, failure, or ECAPA fallback.
- Fix `--num-speakers` when the count is known. Otherwise compare plausible counts with `--min-speakers` and `--max-speakers` and inspect every silhouette score. Similar scores, low separation, or label drift across distance/volume changes means the acoustic labels are unreliable.
- Treat `SPEAKER_XX` as anonymous acoustic evidence. Reconcile it with turn-taking, questions and answers, first-person references, vocabulary, and conversational function in a separate semantic mapping pass.
- Prefer descriptive labels such as `引导者`, `练习者`, `讲述者`, `询问者`, `主持人`, or `受访者`.
- Do not infer real names, age, family relationship, or identity without explicit evidence.
- State confidence and note that very short acknowledgements may be assigned to the wrong role.

### 7. Proofread with intent, without rewriting history

- Correct homophones and ASR errors only when pronunciation, local context, and global intent support the correction.
- Preserve the speaker's meaning, stance, uncertainty, and oral sequence. Remove filler only when it carries no conversational function.
- Keep deliberately garbled, misspelled, random, or test material as spoken. Explain its intended normal reading separately when useful.
- Use `[听不清]`, `[疑似：…]`, or multiple candidates when evidence is insufficient.
- Attribute unverified medical, scientific, financial, legal, or other factual claims to the speaker or course. Do not convert a reported claim into an endorsed fact.
- List important corrections with timestamp, raw candidate, corrected form, evidence, and confidence.

### 8. Compress genuine repetition safely

Compress only contiguous speech from the same role that repeats the same content:

- Use `（重复 X 遍）` when word timestamps or manual counting make the count reliable.
- Use `（约重复 X 遍）` when pauses, modifiers, overlap, or decoder uncertainty prevent an exact count.
- Preserve the full time range.
- Break the compressed span around any new instruction, answer, correction, interruption, or semantic change.
- Never count confirmed zero-duration decoder loops as speech.

### 9. Deliver and verify

Follow `references/output-template.md`. For each recording include:

- media and processing metadata;
- role definitions, evidence, and confidence;
- timestamped corrected transcript;
- corrections and unresolved passages;
- content and intent summary;
- coverage and quality notes;
- explicit disclosure of local models and any external services used.

Before finishing:

1. Compare transcript end coverage with media duration and explain silent or truncated edges.
2. Spot-check the opening, closing, role changes, low-confidence passages, and recovered ranges.
3. Confirm UTF-8 output contains no replacement characters.
4. Recompute source hashes and confirm originals are unchanged.
5. Remove temporary WAVs, debug scripts, raw working files, and failed drafts unless the user requested them.
6. List remaining uncertainty instead of claiming perfect accuracy.

## Resources

- `scripts/transcribe_faster_whisper.py`: local timestamped transcription and bounded recovery passes.
- `scripts/audit_asr_json.py`: deterministic audit for repetition, timestamp collapse, coverage, and encoding anomalies.
- `scripts/diarize_speakers.py`: pyannote or token-free ECAPA acoustic clustering with VAD, auditable embeddings, quality scores, source-integrity checks, and optional ASR timestamp alignment.
- `references/diarization.md`: dependency setup, engine selection, CLI patterns, JSON evidence, and interpretation limits for acoustic speaker clustering.
- `references/output-template.md`: reusable Markdown structure and final quality gates.
