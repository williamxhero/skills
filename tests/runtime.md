# Native Pi acceptance observations

These are **recorded bounded scenarios**, not an executable workflow engine or a replacement tracker. Full acceptance status and exact SPEC wording are indexed in [acceptance.md](../docs/acceptance.md). Raw event streams stay outside this Public repository; do not publish thinking blocks, credentials, private paths or fixture contents without redaction.

## Reproduction boundary

Use a fresh independent external fixture working directory and process for each case, not this source repository or a real application. Copy the three candidate entry directories and the actual required Matt dependencies into one isolated staging root; retain per-file hashes and supply that root explicitly. Do not copy over installed skills. To test missing-resource behavior, use a separate root omitting the named shared dependency; do not repair it by falling back to globals.

Resolve `$PiExecutable` to the actual native Pi CLI and `$Entry` to an absolute candidate entry directory. On the observed Windows host the native CLI was `pi.cmd`, version 1.5.2, not pi-web. From the fixture cwd:

```powershell
& $PiExecutable --no-extensions --no-mcp --no-skills --skill $Entry --no-prompt-templates --provider cpa --model gpt-6.1-sol --thinking medium --tools read,powershell,grep,find,ls --no-session --mode json $SkillCommand
```

`$SkillCommand` begins with `/skill:<entry-name>` plus the bounded request. Add write/edit tools only for explicitly authorized local publication fixtures. All shell tools can write: an allowlist is not a security sandbox. Restrict scope in fixture instructions, inspect tool calls and actual diffs, prohibit GitHub/network writes, installed-resource/config/link changes, push, closure and unrelated paths. A model may launch only required read-only fact explorers in these observations, never final independent reviewers. Verify each child’s actual cwd, new session identity, tools, output and exit separately.

Use stdlib subprocess or native child_process to stream stdout/stderr to external files, wait with a finite timeout and preserve exact argv/cwd/exit. Split JSONL on LF, not Unicode separators; PowerShell 5 redirection may require lossless UTF-16→UTF-8 conversion. Inspect authoritative `message_end`, `agent_settled.aborted`, error/retry events AND exit code. Exit 0 alone does not imply success in JSON mode. `toolUse` interim messages are normal; final error/aborted/length, missing settled, timeout or parse errors are never a pass. Even a clean transport only proves the run finished; inspect tool evidence, persisted state and requirement-specific outcome separately. A truthful blocked outcome passes only the failure contract, not the workflow's completion criteria.

## Observed inputs and checks

| Case | Bounded input | Required observations |
| --- | --- | --- |
| Simple / repeat (inherited #2) | Greeting file `Hello` + LF; change to `Good morning` + LF in SPEC only; local file tracker | Mandatory shared adapter and real grilling/to-spec reads; fresh fact explorer; original-template local issue readback; repeat reuses same issue; greeting unchanged; no tickets/implementation/Git delivery. Not GitHub proof. |
| Missing spec rules (inherited #2) | Spec entry copied without references | Actual failed reads, blocked naming missing files, no writes/publication claim. |
| Architecture no-change | Two-line `greet(name)` plus public unittest, scan those files only | Actual baseline/test, original candidate-stage/vocabulary reads, fresh native probe/explorer and corroborated code evidence; deletion test rejects speculative abstractions; C3 no-change; no SPEC/implementation/rescan. |
| Architecture blocked | Only fixture instructions; requested production module unavailable and external access unauthorized | Inventory/Git readback; C1 blocked, not no-change; no invented source/candidate or external access. |
| Singleton (SIMULATED tracker) | Parent P: A OPEN, verification FAILED, not delivered; B blocked by A, no tickets | Read tracker and real fixture Git state; A stays sole current SPEC; no B ticketing/code, no closure or false delivery. Does not exercise B3 onward or native GitHub dependencies. |
| Missing implementation shared rules | Implementation bundle only, explicit missing root | Failed reads of both canonical spec-sibling resources; blocked before B1; no global fallback/repair/writes. |
| Pure-prose phase | NOTES.md prose-only diff, explicitly B6 only; prior integration is SIMULATED | Read bundled simplify adapter then unchanged upstream instructions, inspect exact diff/provenance; `nothing to simplify`, no reviewers/edits; B7 not triggered from supplied facts. Not a substitute for outer full-diff simplify: this repository's authored Python tests ARE code. |

The exact commands, per-run results, tasks, fixture commits and stream hashes are in the external execution package’s `runtime/` directory, identified by evidence IDs in the matrix. No shipped launcher or fabricated scheduling state is needed. Re-run affected scenarios when entry/resource/adapter bytes change; a docs-only acceptance update does not invalidate unchanged entry observations. Interactive `/reload`, managed discovery/link conflicts, conditional interface/domain/capture branches, successful architecture delivery and real GitHub single/multi/native-relationship idempotence still need their own evidence.
