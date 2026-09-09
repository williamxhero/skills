# SPEC delivery

Read this reference before creating or supervising a SPEC implementation task. Process SPECs sequentially; begin the next only after the current merge, verification, archival, due checkpoint, and default-branch health check.

For each SPEC:

1. Refresh from the default branch, mark the SPEC active, and verify its published SPEC and ticket graph against the locked planning artifacts. A material scope change must return to, verify, and rearchive the same planning task.
2. Read the locked model and effort, select only that pair or a recorded same-or-stronger fallback, and create a fresh project task titled `Implement Needs <NN>: <spec title>` from the latest default branch with a bootstrap-only prompt. Capture the applied model/effort from a fresh task readback and run `validate_planning.py route` for this SPEC. Send the implementation assignment only after `decision: allow`; silent drift stays at bootstrap and is repaired on the same task.
3. Give the child pointers to the requirement, SPEC, tickets and blocking edges, repository instructions, default branch, its delivery-map entry, current train scope, blocker packet contract, and Controller Kernel handoff contract. Its boundary is: apply `implement-spec`; implement every ticket; run narrow ticket and selected per-SPEC tests; apply `code-review` and fix findings; merge the SPEC; clean implementation worktrees; and return the PR, merge commit, closed tickets, test evidence, checks, and blocker evidence. Packaging and deployment remain controller work.
4. Follow it with compact waits. Answer preferences from the default policy. Route `needs_repair` packets through `unblock-development`, then resume the same SPEC task. If it stops before merge without a blocker, send a focused completion follow-up to that task.
5. Treat its final as a claim. Independently verify ticket completion, selected per-SPEC layers and public-contract L3 obligations, integration, and reachability of the merge commit from the default branch. Validate its evidence packet with `test-release-train`; return any failure to the same child and wait again.
6. Close the SPEC and tickets, record merge/test evidence, archive the child, record archival, and update the train. Run any due affected-owner checkpoint before the next SPEC.

Persist each observation before acting. A `running` snapshot records another `wait`; a side question records the Chinese answer and then the unchanged prior action; a child final moves only to `handoff_received`. Verification, archival, and next-SPEC dispatch are separate recorded transitions. Never infer archival from a final message.

On recovery, reconcile the saved child IDs with the fresh task tree before dispatch. If the recorded child still exists, reconnect and resume its exact action. A missing recorded child is state repair or blocker work, never permission to create a duplicate. The next SPEC must be based on the integration revision that contains the independently verified predecessor merge.

One child owns one SPEC through merge and evidence handoff. Do not reuse it for another SPEC or overlap it with another implementation child. The controller retains release context while archived implementation context disappears.

If a later SPEC exposes a defect in an earlier merged increment, add the smallest repair ticket to the current SPEC unless it changes the earlier accepted behavior; in that case plan and deliver a repair SPEC before continuing.
