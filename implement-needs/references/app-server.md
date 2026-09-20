# Codex app-server backend

Use this reference when Host Task API and configured MCP/task connectors are unavailable.
The fallback is the local process `codex app-server --stdio`, communicating through
newline-delimited JSON-RPC messages on stdin/stdout. Start it only after capability
selection and retain the process id and transcript path in controller state.

Before relying on method names or fields, run `codex app-server --help` and send the
protocol `initialize` request. CLI releases may change method names and result shapes;
the controller must use only methods and fields confirmed by the help output or an
actual successful response. A guessed method, guessed task id, or guessed model is a
protocol failure.

The minimum handshake is:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"implement-needs","version":"1"},"capabilities":{}}}
```

Then probe the advertised thread/task creation, thread read, model readback, reasoning
effort readback, turn start, turn status, and shutdown methods. Persist each request and
response as evidence. The probe must not call guessed methods: derive the method map from
`--help`, protocol metadata, or a successful response, then pass that map to the probe
script. The route is selected by `route-codex-task` before creation; the
app-server must receive that exact model and effort. Creation arguments are request
evidence only. A fresh thread read must expose the applied pair, or the controller fails
closed and invokes `unblock-development`.

Use `scripts/app_server_bridge.py` for the native fallback. It reads actual
app-server thread records and a controller-owned identity metadata sidecar. Since the
app-server does not natively provide task/run/attempt/owner identity, a thread is
usable only when the sidecar supplies those fields and agrees with native thread id,
cwd, and project id. The bridge retains asynchronous JSON-RPC notifications while it
waits for the matching response. After `turn/start`, it preserves the returned
`turn_id` and waits for the matching `turn/completed` event by formal
`threadId + turnId`. A title token remains a discovery index, not an identity
substitute.

When the sidecar has `project_id_source: saved_project_readback`, use the saved
project's verified canonical cwd but omit `projectId` from `thread/start`; a
standalone app-server process may not recognize Desktop's saved-project registry.
After creation, require native thread id/cwd and route readbacks plus the non-empty
saved-project evidence. Record this as controller saved-project identity, never as
native app-server project membership.

For an existing thread, enrollment is an explicit `adopt_thread` operation. It may
write the sidecar only after a native `thread/read` proves the formal id, cwd, and
project id. It cannot repair a thread whose native `projectId` is null, and it cannot
invent task/run/attempt/owner values. Those are external facts that must already be
present in the controller registry or an authenticated task-management readback.

Routine backend selection is read-only against an existing managed probe target
apart from `dry_run` mutation checks. It does not create a capability-probe
thread. An explicitly requested live lifecycle qualification uses this lifecycle:

```text
probe -> create fresh thread -> read applied route -> route receipt allow
-> start turn -> await matching `turn/completed` -> bounded persisted-history read
-> independent output verification -> final thread read -> archive -> archive readback

Register the fresh qualification thread as a run-owned `probe` before it starts.
Its cleanup runs after success or failure; `archived: true` from a fresh archive
readback is required before the receipt can be `allow`.
```

The first persisted-history read may be empty, or may return the known transient
`rollout is empty` error. Retry only that condition for a bounded interval and retain
the attempt evidence. A persistent empty history or any unrelated error is a
fail-closed qualification failure.

Planning has one fresh planning thread. Each SPEC has exactly one fresh implementation
thread owning all of its tickets. Never create a ticket implementation thread, reuse a
planning thread for code, run two active SPEC writers against one workspace, or dispatch
the next SPEC before the current one is verified and archived.

An app-server backend has no implied Host Task API archive. Its archive evidence must
include `turn_completed`, `independent_verification_passed`, `final_thread_readback`,
and `controller_archive_record_persisted`. These are recorded in task-census entries
alongside `backend: codex-app-server-jsonrpc` and the transcript hash.

On restart, reload `implement-needs.db` first and recover its oldest pending action.
Reconcile external state, reconnect to an existing child by id, and continue its
recorded next action. Do not create a replacement
until a readback proves the original cannot be recovered; record the original failure
and archive/readback evidence before replacing it.
