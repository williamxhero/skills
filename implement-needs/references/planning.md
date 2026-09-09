# Planning

Read this reference when the controller enters planning. The Controller Kernel remains authoritative for state and message phase.

## Create the one planning task

The planning task owns the complete **Grill → SPECs → tickets → implementation routing** sequence. The controller relays, auto-accepts, validates, and schedules; it does not author or recompute planning decisions.

Before creation, capture the target host's advertised combinations from the sole allow-list in `model-policy.json`. Create `.scratch/<initiative>/planning-record.json` with that capability evidence and a planning route. Bounded planning requires at least `gpt-5.6-terra` + `xhigh`; broad, cross-repository, migration-heavy, or unusually ambiguous planning requires exactly the maximum `gpt-5.6-sol` + `xhigh` pair. Every route contains one supported recommendation, rationale, and at least one supported fallback that is same-or-stronger on the deterministic policy ranks. A fallback must be distinct whenever another same-or-stronger allowed pair exists; because `gpt-5.6-sol` + `xhigh` has no such alternative, that maximum route may repeat itself as its fallback.

Create exactly one fresh saved-project task titled `Implement Needs Plan: <initiative>` with the selected `model` and `thinking`. Its bootstrap prompt permits no exploration, publication, or repository mutation until the controller sends `ROUTE_VERIFIED`. Capture the applied settings from the creation result and a fresh task readback in `.scratch/<initiative>/planning-route-readback.json`, then run:

```text
python <implement-needs>/scripts/validate_planning.py route --record .scratch/<initiative>/planning-record.json --readback .scratch/<initiative>/planning-route-readback.json --expected-run-id <run-id> --target planning --expected-task-id <created-task-id> --receipt .scratch/<initiative>/planning-route-receipt.json
```

Send `ROUTE_VERIFIED` and the full assignment only after `decision: allow`. Retain the complete route receipt: it binds the exact locked recommendation and fallbacks, selected pair, task owner, planning-record hash, readback hash, and canonical receipt identity. A rejection returns a canonical `repair` next action and keeps the same task at bootstrap. Select only a fallback already locked in the record, state why it replaced the recommendation, and capture a fresh readback; an applied pair different from the explicit request is silent drift and cannot begin planning. Unavailable readback is a blocker, not evidence of the requested route.

## Run the delegated Grill

The full assignment points the planner to the requirement, repository instructions, domain vocabulary and ADRs, tracker and default branch, resolved `grilling`, `to-spec`, `to-tickets`, and `test-release-train` protocols, the default policy, and the supported route matrix. Repository exploration and subagents are read-only. Capture a controller-computed product/test-tree fingerprint before the assignment.

The planner returns each complete numbered Grill frontier with a recommended default and rationale for every question, then pauses. For every round, the controller:

1. faithfully relays every question number, question, recommendation, and rationale in Chinese commentary, splitting only for readability;
2. records Chat evidence and acceptance as `implement-needs-standing-authorization`;
3. sends all recommended answers to the same planning task immediately, without requesting or waiting for user confirmation.

The planner alone applies those answers, recomputes the design tree, and returns the next frontier. Continue until it reports an empty frontier. The controller may translate or ask the planner to repair an incomplete round; it does not add, omit, merge, or answer a planning question independently.

## Publish and route

After Grill, the same planning task must:

1. Partition every requirement into exactly one member of the minimum ordered set of coherent SPECs, with acyclic inter-SPEC blockers.
2. Invoke `to-spec` for every SPEC with `confirmation_mode: auto_approve`, `approval_source: implement-needs`, and `approval_text: 同意`; require `spec_state: auto_approved` and provenance `controller_decision`.
3. Invoke `to-tickets`, bypass its quiz under the standing authorization, publish tracer-bullet tickets, and self-check granularity, blocking edges, and acyclicity.
4. Lock one currently supported implementation route and one or more same-or-stronger fallbacks per SPEC from risk and coupling, not ticket count. Tickets inherit their SPEC route; planning never assigns model, effort, or owner per ticket.
5. Initialize release-train owners, repositories, acceptance scopes, public-contract/environment flags, baselines, `checkpoint_size: 10`, and every deterministic L4 checkpoint.
6. Return the completed planning record and artifact evidence without changing product or test code.

Use these exact planning-record shapes; fields not shown are rejected:

```text
route = {recommended:{model,thinking}, fallbacks:[{model,thinking}], rationale}
planning_record = {
  schema_version, run_id, scope, capability_evidence,
  supported_routes:[{model,thinking:[...]}],
  planning_task:{id,generation,route},
  ownership:{grill,specs,tickets,routing}, requirements:[...],
  grill_rounds:[{round,questions:[{number,question,recommendation,rationale}],
    commentary_evidence:[...],acceptance_source,acceptance_evidence:[...],planner_resume_evidence:[...]}],
  frontier_empty,
  specs:[{id,artifact,requirements:[...],blocked_by:[...],auto_approval,
    tickets:[{id,artifact,blocked_by:[...],vertical_slice}],
    ticket_self_check:{granularity,blocking_edges,acyclic,evidence:[...]},
    difficulty,route,checkpoint}],
  release_train:{owners,repositories,acceptance_scopes,public_contract_specs,
    environment_specs,baselines,checkpoint_size,
    checkpoints:[{id,start_spec_index,end_spec_index,specs:[...],final_tail,
      affected_owners:[...],affected_repositories:[...]}]},
  code_read_only:{product_test_tree_before_sha256,product_test_tree_after_sha256,
    changed_product_or_test_paths:[],evidence:[...]}, handoff_evidence:[...]
}
```

Checkpoint policy is fixed, not a planning preference. Use `checkpoint_size: 10`; assign consecutive ordered SPEC groups of ten to `checkpoint-10`, `checkpoint-20`, and so on, followed by one final tail checkpoint named for its ending SPEC index when the total is not divisible by ten. Do not rebalance tails or move boundaries to owner/migration seams.

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
