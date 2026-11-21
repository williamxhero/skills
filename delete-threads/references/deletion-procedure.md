# Delete-threads procedure

## Storage model

For the local Codex installation, `state_5.sqlite` is the thread/index metadata store. The complete event stream is in `rollout-*.jsonl` under:

- `$CODEX_HOME/sessions/YYYY/MM/DD/`
- `$CODEX_HOME/archived_sessions/`

`session_index.jsonl` is a secondary listing index. `history.jsonl` is not the authoritative transcript store and should not be treated as sufficient cleanup evidence.

The helper removes only records selected by the literal title predicate. It also removes thread-owned rows from `guardian_review_feedback`, `thread_attachments`, `thread_dynamic_tools`, `thread_read_receipts`, and `thread_spawn_edges`. It does not guess at arbitrary attachment payloads or delete unrelated logs.

## Title normalization

SQLite may store an automation thread as a long title beginning with `Automation: ` followed by the automation name and prompt. The visible title is the first line after that prefix. Non-automation titles use their first line. Matching is case-sensitive and literal:

| User wording | Helper option |
|---|---|
| `以“学习计划：”开头` | `--title-prefix "学习计划："` |
| `包含“PDCA”` | `--title-contains "PDCA"` |
| `标题为“某个任务”` | `--title-exact "某个任务"` |

Do not use a broad substring when the user asked for a prefix. Do not silently convert punctuation or whitespace.

## Transaction and recovery behavior

1. Discover candidates from the SQLite `threads` table.
2. Validate every candidate's transcript path resolves beneath one of the two allowed session roots.
3. Create a timestamped `.local/delete-threads` restore point containing a consistent SQLite backup, exact transcript copies, the session-index copy, and a manifest.
4. Use one SQLite transaction to delete dependent metadata, spawn edges, and the selected `threads` rows. The main-row delete count must equal the candidate count.
5. Remove only the exact transcript paths and rewrite `session_index.jsonl` without the selected IDs.
6. Run postcondition checks and `PRAGMA integrity_check`.

If the transaction commits but a later file/index operation fails, the operation is partial. Preserve the restore point and stop; do not run the full deletion again without reconciling which stages already completed.

## What is not part of this skill

- Archiving a thread is reversible and is not permanent deletion.
- Deleting an automation is a separate operation and must not be inferred from deleting its historical run threads.
- ChatGPT conversations, remote/durable threads, projects, credentials, logs, and unrelated files are outside scope.
- An active automation may create a new matching run after cleanup; that is expected unless the user separately asks to pause it.
