# Planning

Read this reference when the controller enters planning. The Controller Kernel remains authoritative for state and message phase.

## Create the one planning task

The planning task is the only task allowed to perform planning publication. The
controller must not create a substitute SPEC or ticket, answer a missing Grill question
itself, or start implementation because the planner is slow or returned a summary. Keep
the planner active until the complete machine-checkable handoff exists. A handoff that
does not contain every required planning-record field is incomplete and must be repaired
in the same planning task.

The planning task owns the complete **Grill → SPECs → tickets → implementation routing** sequence. It must invoke `grill-2-tickets` for the Grill-through-tickets portion, then extend that verified handoff with Implement Needs routing and release-train fields. The controller relays, auto-accepts, validates, and schedules; it does not author or recompute planning decisions.

Invoke `route-codex-task` before creation and store its target-host capability evidence and locked route in `.scratch/<initiative>/planning-record.json`. Supply the complete requirement-understanding, SPEC decomposition, per-SPEC implementation-route prediction, and ticket decomposition sequence as the planning profile. Its caller default is `gpt-5.6-sol` + `high` for every scope; keep `planning_xhigh_evidence` empty unless concrete complexity evidence proves `high` inadequate. Let `route-codex-task` own every allow-list, rank, fallback, `xhigh`, readback, drift, and receipt decision.

Create exactly one fresh saved-project task titled `Implement Needs Plan: <initiative>` with the selected `model` and `thinking`. Its bootstrap prompt permits no exploration, publication, or repository mutation until the controller sends `ROUTE_VERIFIED`. Capture the applied settings from the creation result and a fresh task readback in `.scratch/<initiative>/planning-route-readback.json`, then run:

```text
python <implement-needs>/scripts/validate_planning.py route --record .scratch/<initiative>/planning-record.json --readback .scratch/<initiative>/planning-route-readback.json --expected-run-id <run-id> --target planning --expected-task-id <created-task-id> --receipt .scratch/<initiative>/planning-route-receipt.json
```

Send `ROUTE_VERIFIED` and the full assignment only after `decision: allow`. Retain the complete route receipt: it binds the exact locked recommendation and fallbacks, selected pair, task owner, planning-record hash, readback hash, and canonical receipt identity. A rejection returns a canonical `repair` next action and keeps the same task at bootstrap. Select only a fallback already locked in the record, state why it replaced the recommendation, and capture a fresh readback; an applied pair different from the explicit request is silent drift and cannot begin planning. Unavailable readback is a blocker, not evidence of the requested route.

## Run the delegated Grill

The full assignment points the planner to the requirement, repository instructions, domain vocabulary and ADRs, tracker and default branch, resolved `grill-2-tickets` and `test-release-train` protocols, the default policy, and the supported route matrix. `grill-2-tickets` resolves and applies `grilling`, `to-spec`, and `to-tickets`; Implement Needs does not duplicate that workflow. Repository exploration and subagents are read-only. Capture a controller-computed product/test-tree fingerprint before the assignment.

The planner returns each complete numbered Grill frontier with a recommended default and rationale for every question in the visible planning task, then pauses. For every round, the controller:

1. posts only `全部采用推荐选项/答案` in Chinese commentary; the user can inspect the full frontier in the separate visible planning task;
2. records that exact compact text as `acceptance_command` and acceptance source `implement-needs-standing-authorization`;
3. sends only `全部采用推荐选项/答案` to the same planning task immediately, without reproducing questions, answers, or rationales and without waiting for user confirmation.

The planner alone applies those answers, recomputes the design tree, and returns the next frontier. Continue until it reports an empty frontier. The controller may translate or ask the planner to repair an incomplete round; it does not add, omit, merge, or answer a planning question independently.

## Publish and route

Run the complete `grill-2-tickets` workflow in the same planning task. Validate its issue-tree record with that skill's `validate_issue_tree.py`; then the same planning task must:

1. Publish exactly one umbrella SPEC for the complete requirement. It is a tracker container, not an implementation unit: do not create tickets, an implementation task, a route, or a release-train checkpoint for it.
2. Partition every requirement into exactly one member of the minimum ordered set of coherent child SPECs, with acyclic inter-SPEC blockers.
3. Set every child SPEC's GitHub **Parent issue** relationship to the umbrella SPEC. Read the relationship back from GitHub and record evidence; labels, body links, task-list links, and dependency edges do not satisfy this requirement.
4. Invoke `to-spec` for the umbrella and every child SPEC with `confirmation_mode: auto_approve`, `approval_source: implement-needs`, and `approval_text: 同意`; require `spec_state: auto_approved` and provenance `controller_decision`.
5. Invoke `to-tickets` for child SPECs only, bypass its quiz under the standing authorization, publish tracer-bullet tickets, set every ticket's GitHub **Parent issue** to its owning child SPEC, read every relationship back, and self-check granularity, blocking edges, and acyclicity. A body link, task-list link, label, or dependency edge is not a Parent issue.
6. Invoke `route-codex-task` once per child SPEC prediction using whole-SPEC difficulty and risk evidence, not ticket count. Record its route and any `xhigh_evidence`; tickets inherit their child SPEC route and never receive a separate model, effort, or owner.
7. Initialize release-train owners, repositories, acceptance scopes, public-contract/environment flags, baselines, `checkpoint_size: 10`, and every deterministic L4 checkpoint over child SPECs only.
8. Return the completed planning record and artifact evidence without changing product or test code.

Use these exact planning-record shapes; fields not shown are rejected:

```text
route = {recommended:{model,thinking}, fallbacks:[{model,thinking}], rationale}
planning_record = {
  schema_version, run_id, scope, capability_evidence, planning_xhigh_evidence:[...],
  supported_routes:[{model,thinking:[...]}],
  planning_task:{id,generation,route},
  ownership:{grill,specs,tickets,routing}, requirements:[...],
  umbrella_spec:{id,artifact},
  grill_rounds:[{round,questions:[{number,question,recommendation,rationale}],
    commentary_evidence:[...],acceptance_source,acceptance_command,acceptance_evidence:[...],planner_resume_evidence:[...]}],
  frontier_empty,
  specs:[{id,artifact,parent_issue:{parent_id,evidence:[...]},requirements:[...],blocked_by:[...],auto_approval,
    tickets:[{id,artifact,parent_issue:{parent_id,evidence:[...]},blocked_by:[...],vertical_slice}],
    ticket_self_check:{granularity,blocking_edges,acyclic,evidence:[...]},
    difficulty,xhigh_evidence:[...],route,checkpoint}],
  release_train:{owners,repositories,acceptance_scopes,public_contract_specs,
    environment_specs,baselines,checkpoint_size,
    checkpoints:[{id,start_spec_index,end_spec_index,specs:[...],final_tail,
      affected_owners:[...],affected_repositories:[...]}]},
  code_read_only:{product_test_tree_before_sha256,product_test_tree_after_sha256,
    changed_product_or_test_paths:[],evidence:[...]}, handoff_evidence:[...]
}
```

The umbrella SPEC is excluded from ticketing, routing, implementation ownership, and release-train counting. Checkpoint policy is fixed, not a planning preference. Use `checkpoint_size: 10`; assign consecutive ordered child SPEC groups of ten to `checkpoint-10`, `checkpoint-20`, and so on, followed by one final tail checkpoint named for its ending child SPEC index when the total is not divisible by ten. Do not rebalance tails or move boundaries to owner/migration seams.

For any planning or SPEC task, record post-create evidence as:

```text
route_readback = {schema_version,run_id,task_id,target,requested,applied,
  selection,substitution_reason,readback_evidence:[...]}
```

Use `selection: recommended` with `substitution_reason: null`, or `selection: fallback` with a non-empty reason.

An allowed route decision adds `locked_route:{recommended,fallbacks}` and `receipt_sha256` to its receipt. Persist that complete receipt in the lifecycle dispatch and controller-state ownership ledger; an allow-listed pair or free-form evidence pointer without the matching receipt is not dispatch authority.

For SPEC route readback, `task_id` is the one SPEC implementation owner recorded in `implementation_ownership`. A fallback readback is accepted only for that same task ID; a second task is duplicate ownership, not escalation.

## Verify and archive

Treat the planner's final as a claim. Independently compare the product/test-tree fingerprint, verify published artifacts, and complete the record. Return failures to the same task. When corrected, archive that task, refresh controller state, and run the dispatch gate:

```text
python <implement-needs>/scripts/validate_planning.py handoff --record .scratch/<initiative>/planning-record.json --controller-state .scratch/<initiative>/controller-state.json --planning-readback .scratch/<initiative>/planning-route-readback.json --expected-run-id <run-id> --receipt .scratch/<initiative>/planning-receipt.json
```

Only `decision: allow` permits the first implementation task. Rejection returns a canonical `repair` next action, unarchives and resumes the same planner; no second planning task is created. A material later change also reuses that one task with an incremented `generation`, while every implementation task remains archived.

Before dispatching the first SPEC, perform a negative check: the controller and planning
task must not have made product or test changes during planning. If such changes exist,
preserve them and route them for review/recovery; they are not evidence that planning
completed and they must not be silently included in the first SPEC.
