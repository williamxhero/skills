---
name: delete-threads
description: Permanently remove Codex threads whose display titles match an explicit exact, prefix, or substring rule, including their local transcript files and thread index metadata. Use only when the user explicitly requests deletion; do not use for ordinary archiving or for ChatGPT conversations.
---

# Permanently delete Codex threads

Use this skill for requests such as `/delete-threads 以“学习计划：”开头的`.
It performs a destructive local cleanup of Codex thread records; it is not an archive operation.

## Required input and matching contract

1. Extract one unambiguous title predicate from the user's description:
   - `以“X”开头` → `--title-prefix X`
   - `包含“X”` → `--title-contains X`
   - `标题为“X”` / `等于“X”` → `--title-exact X`
2. If the description is ambiguous, asks for a broad category, or does not identify a literal title rule, stop before mutation and ask for the exact rule.
3. Match the Codex UI display title, not the full automation prompt. The helper normalizes automation records whose SQLite title begins with `Automation: `.
4. Scope is Codex threads in the local `$CODEX_HOME` store. Do not delete ChatGPT conversations, projects, automations, or unrelated files.

## Workflow

1. Resolve `$CODEX_HOME` (default `%USERPROFILE%\.codex`) and the project-local `.local` restore directory. Read the current project `AGENTS.md` first.
2. Run the helper in preview mode and inspect every candidate ID, display title, archive state, and transcript path:
   ```powershell
   py -X utf8 scripts/delete_threads.py --title-prefix "X" --dry-run
   ```
3. Refuse to continue if any target is outside the exact title rule, has a missing transcript, or its transcript path is outside `$CODEX_HOME/sessions` or `$CODEX_HOME/archived_sessions`.
4. Because this is irreversible, execute only when the user explicitly asked to delete. Then run:
   ```powershell
   py -X utf8 scripts/delete_threads.py --title-prefix "X" --execute
   ```
5. The helper first writes a local restore point under `.local/delete-threads/<timestamp>/`, then atomically deletes dependent SQLite metadata and the matching rows in `state_5.sqlite`, removes the exact transcript files, and removes matching `session_index.jsonl` records. It must never recursively delete a broad directory.
6. Verify the helper's postconditions: zero matching thread rows, no matching transcript files, no matching session-index IDs, `PRAGMA integrity_check` passes, and the backup manifest exists.
7. Re-read the Codex thread/archived-thread lists when available. If the UI still shows stale entries, tell the user to restart Codex; do not retry the destructive mutation.

## Safety and stopping rules

- Never delete based on a guessed regex or a natural-language category. Convert the request to one literal exact/prefix/contains predicate.
- Never delete the current thread if its own ID is known; ask the user to continue from another thread first.
- Keep the `.local` restore point even though the thread is removed from Codex. Report its path; it is the safety backup, not an additional live thread.
- If the database transaction succeeds but file/index cleanup fails, stop, preserve the restore point, report the partial state, and do not replay the whole deletion blindly.
- An active automation can create new matching threads after cleanup. Do not pause or delete the automation unless the user separately requests that.
- Do not claim permanent deletion from an archive API alone. Permanent deletion requires both the SQLite/index cleanup and transcript-file removal.

See `references/deletion-procedure.md` for the storage model, failure handling, and evidence retained by the helper.

## After-use review: keep only reusable lessons

After the requested cleanup and its postcondition checks are reported, spend a few lines on this run: what the user corrected, which step was misread or skipped, which gate failed to catch something (for example a title rule that turned out ambiguous), where work was redone, which inputs should have been asked for first.

- **Qualify before writing.** The lesson must recur on a similar task and fit one actionable "when you see X, do Y" constraint. Skip one-off environment failures, facts specific to this run, unverified guesses, and anything an existing rule already covers.
- **Write it into this skill.** Prefer merging it into an existing step, gate, or trigger over adding another bullet; if the same lesson shows up again, restructure instead of appending more sentences. Delete requirements this run disproved; leave no contradictory bullet behind.
- **Edit only the copy actually loaded this run** — its `SKILL.md` or the references shipped in this directory. When another tool manages that copy (for example a CC Switch managed copy), edit its maintenance source and state that the change is not installed; never overwrite the managed copy. Touch pinned upstream or specialist assets only through their adapter; do not import another skill's rules, create a second source of truth, or write secrets and private paths.
- **Do not change this run's outcome.** The review grants no extra authorization: no commit, push, install, deploy, or rewritten evidence.
- **Otherwise pass.** No file edit, no changelog entry, no version bump. At most one closing line — "sedimented: …" or "nothing to sediment" — and do not replay the review.
