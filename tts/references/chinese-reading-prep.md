# Chinese Reading Preparation

## Normalize by meaning, not by character shape

Use context-specific rules such as these:

| Source pattern | Spoken form | Notes |
| --- | --- | --- |
| `2024-01-02` | `二零二四年一月二日` | Treat as a calendar date. |
| `2024年` | `二零二四年` | Read years digit by digit. |
| `09:30` | `九点三十分` | Treat as clock time. |
| `17.2%` | `百分之十七点二` | Put 百分之 before the number. |
| `5W` | `五万` | Only when `W` clearly means 万. |
| `20cm涨停` | `二十厘米涨停` | Use market context, not English letter names. |
| `600519` | `六零零五一九` | Read digit by digit only when identified as a stock code. |
| `T+1` | `T 加一` | Preserve the market convention while making the operator audible. |
| `A股` / `ETF` / `IPO` | spaced letter reading | Add suitable boundaries so the engine does not merge the letters into nearby Chinese. |
| `16—18年` | explicit year range | Infer the omitted century only when the surrounding text establishes it. |

Do not apply one rule to every number. The same digits can denote a year, quantity, price, percentage, code, rank, chapter, or identifier, and each may require a different reading.

## Handle polyphonic words with phrase-level rules

Pay special attention to names and words containing characters such as `重`、`行`、`长`、`空`、`量`、`转`. Their pronunciation depends on the complete phrase and sentence.

1. Preserve the full source word in the publication layer.
2. Add the longest unambiguous phrase to a pronunciation lexicon.
3. Prefer SSML phonemes or a pronunciation dictionary when the engine supports them.
4. If the engine lacks pronunciation controls, use a tested homophone spelling only in the spoken-text layer.
5. Never perform a character-wide replacement; it will corrupt unrelated words.
6. Version the lexicon and include that version in cache keys and manifests.

## Add pauses where a speaker would breathe

- Keep titles and section headings as separate segments with a slightly longer following pause.
- Split long sentences after complete clauses, enumerations, contrasts, causes, conditions, or conclusions.
- Avoid splitting a person's name, stock name, number plus unit, paired quotation marks, parentheses, or an abbreviation.
- Convert malformed extraction punctuation into ordinary Chinese punctuation before segmenting.
- Collapse decorative symbol runs instead of asking the engine to pronounce them.
- Keep pauses modest. Excessive commas make narration hesitant; too few boundaries make it breathless.

## Separate prose from evidence artifacts

- Preserve transaction tables and OCR evidence in their archival outputs, but omit them from article narration unless the user explicitly requests an accessible reading.
- Narrate image OCR only when it contains coherent prose and the recognition quality is adequate.
- Replace an empty video shell with a short neutral note only if that fact matters to the article; otherwise omit it from narration.
- Remove duplicated titles, source labels, and timestamps from the spoken layer after verifying that they are metadata rather than prose.
- Keep all cleanup reversible by retaining the source artifact or an audit mapping.

## Audit the prepared text

Generate a machine-readable or human-readable report that includes:

- remaining Latin tokens and whether each is intentionally letter-spelled;
- unusual symbols and the chosen spoken treatment;
- known polyphonic phrases and the rule applied;
- sentences exceeding the configured length threshold;
- numeric tokens that did not match a known semantic pattern;
- source sections excluded as non-prose;
- substitutions that differ between publication and spoken layers.

Review flagged items instead of assuming that a clean regular-expression pass means the text will sound natural.

## Build a representative pronunciation sample

The sample should deliberately cover:

- author and trader names;
- at least one polyphonic phrase;
- a full date, a year, and a clock time;
- a percentage, decimal, amount, and stock code;
- a range and an arithmetic or trading operator;
- English-letter abbreviations adjacent to Chinese;
- uncommon punctuation or symbols found in the corpus;
- a paragraph long enough to test breathing and clause segmentation.

Listen for correctness, continuity, stress, pace, and pause placement. Inspect the spoken text at the same time. ASR back-transcription is useful for spotting omissions or major substitutions, but manual listening remains the acceptance gate for naturalness.
