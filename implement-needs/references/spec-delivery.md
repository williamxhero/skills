# SPEC delivery

The SPEC task boundary is mandatory. Do not implement a SPEC in the controller, in the
planning task, or in a ticket task. The controller creates exactly one fresh task for
the SPEC, validates its applied model/effort, then waits on that task until it returns a
handoff. If task creation, route readback, or the wait operation is unavailable, use
`unblock-development`; never fall back to same-thread implementation.

Read this reference before creating or supervising a SPEC implementation task. Process SPECs sequentially; begin the next only after the current merge, verification, archival, due checkpoint, and default-branch health check.

For each SPEC:

Before creating the SPEC task, pass the thread-registry reconciliation gate. After
creation, complete route readback and send the implementation assignment in the same
controller turn. If the task stops at bootstrap or the parent misses that handoff,
record `abandoned_after_bootstrap`, archive the task, and read back archival state
before any next-SPEC or repair dispatch.

1. Refresh from the default branch, mark the SPEC active, and verify its published SPEC and ticket graph against the locked planning artifacts. A material scope change must return to, verify, and rearchive the same planning task.
2. Invoke `route-codex-task` with the locked whole-SPEC prediction and current target host, then create a fresh project task titled `Implement Needs <NN>: <spec title>` from the latest default branch with a bootstrap-only prompt. Capture the applied settings from a fresh task readback and run `validate_planning.py route --expected-task-id <created-task-id>` for this SPEC. Send the implementation assignment only after `decision: allow`; persist the complete allow receipt in the lifecycle event and ownership ledger. Repair every rejected route on the same task boundary.

At SPEC entry and after recovery, show this Chinese controller status with the persisted applied pair:

```text
SPEC {spec_id} 当前由任务 {task_id} 执行；实际模型：{model}；推理强度：{thinking}；路由：{selection}。
下一步：{next_action}
```

`{model}` and `{thinking}` are the actual validated readback/persisted values, never placeholders for a requested route.
3. Give the child pointers to the requirement, SPEC, tickets and blocking edges, repository instructions, default branch, its delivery-map entry, current train scope, blocker packet contract, implementation ownership ledger, and Controller Kernel handoff contract. Its boundary is: apply `implement-spec` for its task graph, PR, review, test, and cleanup semantics while overriding ticket-worker topology locally; implement every ticket inside this same task, worktree, branch, and PR; run narrow ticket and selected per-SPEC tests; apply `code-review` and fix findings; merge the SPEC; clean implementation worktrees; and return the PR, merge commit, closed tickets, ticket-level commit evidence, test evidence, checks, and blocker evidence. Packaging and deployment remain controller work.
4. Follow it with compact waits. Answer preferences from the default policy. Route `needs_repair` packets through `unblock-development`, then resume the same SPEC task. If it stops before merge without a blocker, send a focused completion follow-up to that task.
5. Treat its final as a claim. Independently verify ticket completion, selected per-SPEC layers and public-contract L3 obligations, integration, and reachability of the merge commit from the default branch. Validate its evidence packet with `test-release-train`; return any failure to the same child and wait again.
6. Close the SPEC and tickets, record merge/test evidence, archive the child, record archival, and update the train. Run any due affected-owner checkpoint before the next SPEC.

The child final is an event, not a completion state. The controller must immediately
perform steps 5 and 6. In particular, a child commit is not a merged commit, and an
idle child is not an archived child. Do not emit a controller final after receiving the
child final; only the terminal validator may authorize that. If the child stopped before
its handoff, send a focused continuation to the same child or route its blocker, then
wait again.

Persist each observation before acting. A `running` snapshot records another `wait`; a side question records the Chinese answer and then the unchanged prior action; a child final moves only to `handoff_received`. Ticket progress records each ticket's SPEC owner task ID, blockers, commits, tests, and tracker state in `implementation_ownership`; `ticket_implementation_artifacts` stays empty. Verification, archival, and next-SPEC dispatch are separate recorded transitions. Never infer archival from a final message.

On recovery, reconcile the saved child IDs with the fresh task tree before dispatch. If the recorded child still exists, reconnect it with the exact persisted route receipt and resume its exact action. Revalidate that the receipt still names the same SPEC owner and exact locked recommendation or same-or-stronger preapproved fallback. A missing recorded child or changed receipt is state repair or blocker work, never permission to create a duplicate. The next SPEC must be based on the integration revision that contains the independently verified predecessor merge.

Every wait is an active supervision step, not a pause for user input. On each snapshot,
record child status, last progress timestamp, current phase, and next action. If the child
is idle without a verified handoff, send a focused continuation in the same task; if it
reports a blocker, route it immediately; if it is complete, verify before archive. Do not
emit a controller final or leave the controller turn after merely reporting that the
child is running.

Before declaring the SPEC complete, perform a task-tool archival readback for its
implementation task and store it beside the merge and test evidence. Closing the SPEC
or tickets in GitHub does not close the Codex task; both states require independent
verification. Treat a child visible as `idle` or `notLoaded` as unarchived until the
archive operation and post-operation readback succeed.

One child owns one SPEC through merge and evidence handoff in `whole-spec` mode. Do not reuse it for another SPEC or overlap it with another implementation child, and do not create ticket implementation tasks, threads, worktrees, branches, or PRs. When the run is `single-ticket-line`, the designated controller task owns the global queue and no implementation child is created; its queue position, blocker readback, commit, tests, and merge evidence are the ownership boundary. Repair tasks and read-only exploration or review tasks are role-limited helpers only. The controller retains release context while archived implementation context disappears.

If a later SPEC exposes a defect in an earlier merged increment, add the smallest repair ticket to the current SPEC unless it changes the earlier accepted behavior; in that case plan and deliver a repair SPEC before continuing.
