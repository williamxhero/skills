---
name: quote-mux-server-backfill
description: Audit, obtain, import, and verify real missing QuoteMux data on the user's yosef-server. Use when the user reports a QuoteMux/MarketHub data gap, asks to repair historical daily or intraday data, wants a formal readiness gate unblocked, mentions "补齐小电脑 QuoteMux 数据", or asks to fill data via providers, SuperMind, or a trustworthy web source.
---

# 补齐小电脑 QuoteMux 数据

Repair confirmed data gaps in the live QuoteMux facts on `yosef-server`. Treat a successful import as incomplete until the original audit passes for the exact target keys and fields.

## Non-negotiable rules

- Use the `yosef-server` skill first. Verify the live service and deployment layout before acting; do not assume an old path, schema, release, or data version.
- 补数期间默认保持 `markethub-api.service` 可用。不得为方便起见停止、重启或部署服务；优先采用不中断服务的只读探针、分批写入和在线校验。确有必要停服时，缩短中断窗口，在命令的 `trap`/`finally` 恢复路径中立即启动服务；恢复后必须同时确认 `systemctl is-active`、`/api/health`、active release 和 data version，才能继续写入或报告完成。
- Use real source-native data only. Never invent OHLC, `amount`, `volume`, minute bars, money flow, price bands, or memberships; do not distribute a daily value across minutes.
- Preserve existing correct data. Prefer narrowly scoped, idempotent upserts for missing keys/fields. Do not delete or replace a whole date without a verified complete replacement and explicit authority.
- Keep source lineage: provider/source name, request range, target data version, artifact hash, field mapping, row count, and before/after coverage.
- If a provider, API, browser/CDP session, login, remote notebook, or required source field is unavailable, stop that branch immediately. Report the exact failed probe and do not mark the gap filled.
- Treat every zero-row response as no coverage even when its schema is correct. Never convert missing money flow or another absent fact to a numeric zero.
- Do not treat a legacy 239-bar or null-`amount` minute archive as a valid source for a 240-bar-plus-amount contract.
- Before any SuperMind, browser, online-notebook, website, or long-running crawl route, invoke `$crawler` and follow every one of its current rules. If the selected route is SuperMind, let `$crawler` invoke `$supermind-crawler`; do not bypass that dependency chain. The applicable browser/session, preflight, bounded-run, Parquet, hash, cleanup, kernel, and fail-closed requirements are mandatory for this skill.

## 1. Establish the live baseline

1. Check `ssh yosef-server`, `markethub-api.service`, `/api/health`, the active release, and its data version.
2. Locate the existing audit/readiness script and the source facts. Read the relevant code and schema before writing.
3. Run the original audit read-only with its original date samples and membership profile. Save a timestamped JSON report under `/data/markethub/audit/<remediation-id>/`.
4. Export exact missing detail before filling: date, market, code, concept ID where relevant, and each missing field. Count both unique instruments and dependency rows; they are not interchangeable.
5. Validate the audit's eligibility scope before sourcing data. Require effective concept membership plus stock listed/delisted eligibility on the audited date. If a counted row is ineligible, fix and test the audit logic; never manufacture a fact to satisfy a false blocker.
6. Label the baseline as sampled or exhaustive, with the exact dates, instruments, fields, and window inspected. Never extrapolate a sampled finding into full-market or full-window completeness.

Read `references/quotemux-yosef-contract.md` for the known layout, core tables, and integrity rules. Re-verify all facts against the live release.

## 2. Classify every gap before choosing a source

Classify each gap by both fact and contract:

| Gap | Required evidence | Typical valid route |
| --- | --- | --- |
| Stock daily / money flow | Exact eligible concept-member keys, including suspended members when the fact contract requires them | Provider endpoint that covers those keys; otherwise SuperMind `skip_paused=False` member stats |
| Price band | Eligible stock-date keys, authoritative trading status, and effective trading rules | Source-native limit prices, or deterministic rule derivation with complete evidence |
| Concept daily OHLCV/amount | Concept identity/alias valid on date and all required fields | Source-native concept/THS/TI daily data, then mapped/imported with lineage |
| Concept coverage | Effective membership scope for the date | Historical membership source or verified snapshot; never infer membership from today's catalog |
| Stock 1m | 240 standard timestamps, no unexpected times, non-null amount | Source whose raw minute fields include amount; reimport the complete day atomically |

For any provider candidate, first run a read-only probe for one target day and representative edge keys. Confirm date coverage, raw fields, units, market-code mapping, and enough rows. Verify edge-market codes against an authoritative exchange or provider mapping; do not guess aliases from code shape. An empty provider response is a blocker, not a zero-valued fact.

### Important membership rule

Build targets from the effective `ref.concept_stock_membership` interval for each audited day, intersected with stock listed/delisted eligibility, not from an "active stocks" or positive-turnover universe. A broad daily money-flow backfill can succeed while still missing suspended or low-liquidity concept members. Keep delisted-before-date or not-yet-listed members out of dependency blockers unless the governing contract explicitly includes them.

## 3. Select and run the remediation route

### Source discovery is not provider-limited

Do not stop at Tushare, SuperMind, or providers already installed locally. For each exact residual, enumerate credible alternatives appropriate to the required fact: licensed research data, authenticated research platforms, exchange or vendor historical downloads, provider-native web archives, and preserved historical captures. Probe one representative exact key per source before a bounded batch, and record availability, identifiers, raw fields, units, trading-time grid, access condition, and source timestamp.

An alternative closes a gap only when it satisfies the existing fact contract. A source that lacks `amount`, a dated identity, or a historical capture/publication time does not close the corresponding fact. For strict PIT membership, prioritize archival datasets that carry capture/publication time or immutable historical snapshots; a current query with a historical effective date is not a substitute. Keep source selection narrow and evidence-led. Do not bulk scrape, create an account, or purchase a subscription without user authorization.

### Local free-stockdb candidate

Treat a user-provided local `free-stockdb` release as an additional candidate for stock daily or minute recovery, not as an automatically trusted provider. Use it only in an isolated local staging directory: verify any published release checksum, retain the release URL/version and synced-data timestamp or manifest when available, and never point its updater or writable database at MarketHub facts. Start its query service with an isolated configuration if the bundled default port conflicts locally; do not alter production networking or reserved-port policy to accommodate it.

Before accepting any free-stockdb output, probe an exact target key and retain the raw response. Confirm historical date coverage, market/code identity (especially edge markets), raw OHLCV plus `amount`, units, and the exact 240 timestamp contract. Its raw grid may contain non-contract timestamps; select only source-native rows that exactly match the declared 240-slot MarketHub grid, never synthesize or forward-fill a missing slot. A successful current-date A-share query does not prove historical or B-share coverage. Cross-check a representative overlapping A-share day against an independent credible source before a bounded import, and preserve the release/artifact hashes and query evidence in lineage.

### Instrument scope

When the governing remediation scope says to provide A-share data only, exclude B shares from target generation, completeness counts, and backfill attempts: SHSE `900xxx` and SZSE `200xxx`. Preserve any already stored B-share facts unless separate explicit deletion authority is given. Beijing Stock Exchange securities are not Shanghai/Shenzhen B shares and remain in scope unless the user narrows the universe further.

### Existing QuoteMux/provider route

Use existing repository maintenance/backfill scripts when they match the exact fact and field contract. Use a new remediation-specific checkpoint/state file when a historical task must be rerun; do not silently trust an old completion marker.

Before execution, prove that the script's target selector includes every exported missing key. If it filters to active/positive-amount symbols, it cannot close a dependency-based gap by itself.

Run only the requested dates or explicit target key list. Record provider probe output and row counts. After each bounded batch, re-query the exact target keys rather than only printing the script's claimed write count.

For money flow, require an actual source record and preserve unavailable subfields as null. Do not infer flows from turnover, price direction, or another aggregate, and do not insert zero rows merely to close the readiness count.

### Exact historical stock-daily recovery

- Treat a failed daily contract as possibly caused by an absent row, null required field, missing adjustment factor, or false suspended marker. Snapshot and classify existing target rows before deciding between insert and replacement.
- When using Tushare `daily`, normalize `vol` from lots to shares (`* 100`) and `amount` from thousand CNY to CNY (`* 1000`); preserve the raw response and unit mapping in the manifest.
- Accept a Tushare `suspend_d` event as a full-day suspension only when the exact date has `suspend_type=S`, no daily row, and `suspend_timing` is null or empty. Do not treat `R` events or intraday suspension intervals as full-day evidence.
- Resolve historical exchange aliases from an authoritative mapping artifact. For BSE code migrations, keep the current canonical code in MarketHub while recording and querying the dated provider alias; never infer an old code from the new code's digits.
- For B shares, probe source-native endpoints instead of trusting a generic cached history wrapper. Require a source with OHLC, volume, and amount, obtain the adjustment factor from an evidenced source, and cross-check overlapping rows against another independent source such as TDX. Reject rounded-price or missing-amount coverage as insufficient.
- Replace an existing target row only under an explicit exact-target replacement policy after the staged artifact is complete and independently verified. Save the before-image, restrict replacement to the exact key set, and verify every normalized value after apply.
- Freeze the data version during a full-window audit, checkpoint batches, and resume only against the same version. A successful targeted repair is not complete until the exhaustive API contract audit reports zero incomplete batches and zero missing code-dates.

### Concept-daily mapping and derivation

Treat source concept mapping as partial coverage. Validate each provider concept identifier or alias on the target date; if `concept_thscode` is absent, invalid, or non-tradable for that source, leave the concept unmapped and fail closed.

Use an explicit derived source such as `derived_core` only when the QuoteMux capability contract permits derivation. Before importing, record the exact formula, units, member-date scope, weighting, ST policy, suspension policy, and missing-member behavior. Identify the row as derived, retain its input lineage, and fill only missing keys or fields without overwriting existing non-null source-native values.

### Deterministic price-band derivation

Derive a price band from trading rules only when all inputs are evidenced for the target date: an official rule effective on that date, the authoritative previous close, price tick, rounding method, market/security class, and ST or risk-warning status. Record the rule version and a distinct derived source. If any input or rule transition is ambiguous, leave the key missing.

### SuperMind route

This route writes/executes inside `D:\WILL\STOCK\supermind_proj`, so use `cross-project-delegation` and create/coordinate a task owned by that project when the user authorizes it. The coordinator must not edit its files directly.

The SuperMind task must:

1. Invoke `$crawler` before browser or notebook access. `$crawler` must then invoke `$supermind-crawler`; the QuoteMux skill must not duplicate, weaken, or bypass either skill's contract.
2. Freeze the live MarketHub data version and fetch explicit target dates and keys only.
3. Verify every artifact's schema and coverage before importing it into yosef-server.

For a historical all-market minute repair, parameterize a hard-coded date runner only in the owning SuperMind project. First smoke-test one day and require 240 standard bars plus `amount` for every imported code before a wider run.

### Web/source acquisition route

Invoke `$crawler` whenever the valid source is an authenticated browser, remote notebook, website, or long-running archive job. Follow its complete current contract rather than duplicating or weakening it here. Prefer an official or source-native dataset; retain download provenance and checksums. Do not scrape a display page if it cannot establish the required field semantics.

## 4. Import safely

1. Stage raw artifacts away from production facts.
2. Validate primary-key uniqueness, date range, market-code normalization, fields, units, and source row counts.
3. For a complete day-level replacement, validate the entire staged day first and publish it atomically. Otherwise upsert only explicit missing keys/fields, without overwriting non-null proven values.
4. Write `loaded_at` and the source/lineage fields supported by the target schema.
5. Save a compact import manifest and before/after counts under `/data/markethub/audit/<remediation-id>/`.
6. After every bounded batch, re-read the live release link and data version before validating or continuing. Stop if either changed unexpectedly; do not attribute results to a stale baseline.
7. Hash raw and normalized artifacts plus manifests. Move rejected or superseded artifacts into an explicit `rejected/` area; never let them be mistaken for import candidates.

## 5. Verify to completion

Run all checks below after import:

- Exact missing-key export reports zero for each repaired fact and date.
- Concept daily coverage equals the effective concept scope; every required OHLCV/amount field is non-null.
- Dependency audit is zero for stock daily, price bands, and money flow across the effective membership rows.
- Each required minute code-day has exactly 240 standard timestamps from 09:31–11:30 and 13:01–15:00, includes 14:59, has no unexpected times, and has non-null `amount` on all 240 bars.
- Re-run the original formal readiness audit with the same profile and samples. Compare blockers to the baseline; only report success if the requested blockers are gone.
- Check `/api/health` after any server-side action.
- Export the exact remaining keys after the final audit, including eligibility context and both dependency-row and distinct instrument counts.
- Report minute verification at its actual scope. A repaired sample code-day proves only that sample; it does not prove all-market coverage or completeness across the historical window.

If a target cannot be filled, leave the gate fail-closed and hand off: failed probe, exact missing-key artifact, source coverage limitation, completed work, and the smallest safe next action.

## Required completion report

State the live release and data version, remediation ID, sources used, target dates/keys, artifacts and hashes, imported row counts, before/after audit counts, readiness result, health result, and exact remaining blockers. Also state whether MarketHub remained continuously available; if it did not, give the reason, outage window, and recovery-validation evidence. Distinguish sampled from exhaustive checks and a source/provider block from a data-quality block. If `readiness=false`, say that the run partially repaired the audited gaps and list the real residual blockers; never claim the dataset or requested window is fully backfilled.
