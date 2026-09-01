---
name: tts
description: Prepare Chinese prose for natural text-to-speech, synthesize long-form neural audio, and verify pronunciation, pauses, numbers, symbols, resumability, and output integrity. Use for TTS, article narration, audiobook-style conversion, improving mechanical Chinese speech, or requests to narrate with the user's own voice such as “我的声音”“我的音色”; do not use for transcribing existing recordings.
---

# Natural Chinese TTS

Produce listenable Chinese narration, not a literal character stream. Preserve the source text separately, create an auditable spoken-text layer, test difficult readings before bulk synthesis, and verify the final audio as a deliverable.

## Choose the workflow

- For a short passage, prepare the spoken text, synthesize it, and listen-check the difficult sentences.
- For a long article or corpus, run preparation and audit first, generate a representative sample, then synthesize in resumable batches.
- For transcription of existing recordings, use the ASR skill instead.

## Route the user's own voice

When the user says “我的声音”, “我的音色”, or otherwise explicitly requests their cloned voice, read [Personal voice route](references/personal-voice-route.md) completely and use `scripts/synthesize-my-voice.ps1`. Treat the user-approved `C_late` checkpoint and `energetic` style as the default voice identity. Keep the sample gate for pronunciation and prosody on long or difficult material; the approved checkpoint does not require another identity comparison unless the user asks for one.

## Keep two text layers

Never silently overwrite the publication copy.

- The publication layer preserves the author's wording after removing confirmed page chrome, watermarks, duplicate metadata, and unrelated OCR noise.
- The spoken-text layer may expand abbreviations, normalize numbers, add punctuation, and apply pronunciation hints solely to improve speech.
- Record or retain enough mapping information to trace spoken text back to the source.

## Prepare Chinese for speech

1. Remove only confirmed non-content material: watermarks, navigation text, repeated title blocks, standalone timestamps, and extraction artifacts.
2. Classify tables, transaction records, image OCR, and video shells before narration. Do not read dense trading tables as if they were prose.
3. Normalize numbers and symbols by context. Handle dates, years, clock times, percentages, decimals, money, ranges, stock codes, trading notation, and Latin abbreviations separately.
4. Resolve polyphonic characters in the complete word or phrase. Prefer exact phrase entries in a pronunciation lexicon; never replace one Chinese character globally.
5. Insert pauses at semantic boundaries. Preserve sentence meaning, keep paired punctuation together, and split long sentences at clauses rather than arbitrary character counts.
6. Save a spoken-text preview and an audit report before synthesis. Flag unresolved Latin tokens, unusual symbols, suspiciously long sentences, and known polyphonic contexts.

Use [Chinese reading preparation](references/chinese-reading-prep.md) for concrete normalization and review rules.

## Require a sample gate

Before a long run, synthesize a short sample that includes the corpus's hard cases: names, polyphonic words, dates, clock times, percentages, decimals, amounts, stock codes, ranges, operators, abbreviations, symbols, and one long paragraph.

Review both the prepared text and the audio. Revise the lexicon, normalization, punctuation, voice, rate, or pitch until the sample is acceptable. Do not start the full batch merely because synthesis succeeds technically.

## Make long synthesis resumable

- Use a natural neural voice with configurable voice, rate, pitch, and volume.
- Split by semantic boundaries and keep segments below the engine's practical text limit.
- Bound concurrency and retry transient failures with backoff.
- Cache each segment by a content hash that includes the spoken text, voice settings, and normalization or lexicon version.
- Write segment output to a temporary `.partial` file, validate it, then rename atomically.
- Insert short silences between sections when needed; do not encode pauses as spoken punctuation names.
- Concatenate into a temporary final file, validate it, then publish atomically.
- Maintain a manifest or checkpoint so an interrupted run reuses valid segments instead of restarting.

## Verify before publishing

- Confirm expected article, segment, and final-file counts.
- Confirm every final audio file exists, is non-empty, decodes successfully, and has the intended codec, sample rate, and channel layout.
- Confirm there are no leftover `.partial` files or silently skipped sections.
- Store duration, size, and preferably a checksum in the manifest.
- Review the audit report: unexplained symbols, unresolved abbreviations, and extreme sentence lengths must be resolved or explicitly documented.
- Listen to representative samples from the beginning, middle, and end, including every difficult reading category.
- ASR back-transcription may expose gross errors, but it is secondary evidence; it cannot prove that pronunciation and prosody sound natural.

Keep previous audio until the replacement passes all checks. At handoff, report the engine and voice settings, output locations, article and duration totals, verification performed, and any remaining pronunciation uncertainty.
