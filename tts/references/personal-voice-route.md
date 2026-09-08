# Personal Voice Route

Use this route only when the user explicitly asks for “我的声音”, “我的音色”, or their cloned voice.

## Engine

- Project: `D:\WILL\ASR.TTS\voice_clone_tts`
- Engine: GPT-SoVITS v2ProPlus
- Final selection: `r2_A_early`
- GPT checkpoint: `checkpoints/gpt/will_v2proplus_r2-e5.ckpt`
- SoVITS checkpoint: `checkpoints/sovits/will_v2proplus_r2_e4_s408.pth`
- Approved default style: `identity` (本人相似度优先)
- Model API: `http://127.0.0.1:9880`

Treat `config/model.json` and `config/styles.json` as the runtime sources of truth. Require the checkpoints above and require `identity.approved` to remain true. Report any mismatch before synthesis instead of silently changing the model or style.

## Invocation

Run the bundled wrapper with absolute paths:

```powershell
& "C:\Users\will\.codex\skills\tts\scripts\synthesize-my-voice.ps1" `
  -InputPath "D:\path\article.md" `
  -OutputStem "D:\path\article_my_voice"
```

Omit `-OutputStem` to publish under the project's `outputs` directory. The wrapper checks the selected checkpoints, starts the API in the background when necessary, waits for readiness, and then invokes the resumable batch pipeline.

Expected outputs are `.wav`, `.mp3`, `.srt`, `.spoken.txt`, `.manifest.json`, and a `_segments` directory. Verify all outputs according to the main skill before handoff.

For a long document or difficult names, dates, numbers, symbols, or mixed Chinese-English text, prepare and synthesize a short representative sample first. Continue with the full input only after the user accepts the sample.
