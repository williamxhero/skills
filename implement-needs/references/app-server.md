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
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
```

Then probe the advertised thread/task creation, thread read, model readback, reasoning
effort readback, turn start, turn status, and shutdown methods. Persist each request and
response as evidence. The probe must not call guessed methods: derive the method map from
`--help`, protocol metadata, or a successful response, then pass that map to the probe
script. The route is selected by `route-codex-task` before creation; the
app-server must receive that exact model and effort. Creation arguments are request
evidence only. A fresh thread read must expose the applied pair, or the controller fails
closed and invokes `unblock-development`.

The normal lifecycle is:

```text
probe -> create fresh thread -> read applied route -> route receipt allow
-> start turn -> read status/handoff -> independent verification
-> final thread read -> persist app-server archive record
```

Planning has one fresh planning thread. Each SPEC has exactly one fresh implementation
thread owning all of its tickets. Never create a ticket implementation thread, reuse a
planning thread for code, run two active SPEC writers against one workspace, or dispatch
the next SPEC before the current one is verified and archived.

An app-server backend has no implied Host Task API archive. Its archive evidence must
include `turn_completed`, `independent_verification_passed`, `final_thread_readback`,
and `controller_archive_record_persisted`. These are recorded in task-census entries
alongside `backend: codex-app-server-jsonrpc` and the transcript hash.

On restart, reload controller-state, task-tree, and task-census first. Reconnect to an
existing child by id and continue its recorded next action. Do not create a replacement
until a readback proves the original cannot be recovered; record the original failure
and archive/readback evidence before replacing it.
