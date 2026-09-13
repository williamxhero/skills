---
name: grill-2-tickets
description: Autonomously turn a software requirement into published GitHub planning artifacts by visibly executing and recording Grill rounds, creating one umbrella SPEC, partitioning child SPECs, creating tickets, and verifying Parent issue relationships. Use for “Grill 2 Tickets”, default planning through tickets, or an orchestrator's no-confirmation planning phase.
---

# Grill 2 Tickets

## Hard boundary

This skill has exactly one terminal condition: a validated planning handoff. It must
never edit product or test code, create implementation tasks, merge branches, build
artifacts, or deploy. In an orchestrated run, its final is a handoff to the parent, not
a user-facing workflow completion. The parent independently validates the issue tree
before dispatching implementation.

Turn a requirement into an approved, published, hierarchy-verified GitHub issue tree. Stop after tickets and the planning handoff; do not implement, merge, package, or deploy.

## Workflow

1. Resolve and read the installed `grilling`, `to-spec`, and `to-tickets` protocols. Treat invocation as standing authorization to accept recommended defaults and publication confirmations.
2. Create a fresh planning run identity and evidence namespace before reading prior artifacts. Record a run id, source-document fingerprint, baseline issue census, and the output path for the new issue-tree. An old issue-tree, old issue-number range, or another task's final message is reference material only, never evidence for this run.
3. Run Grill to an empty question frontier. Every round must be a real `grilling` round: show every numbered question, recommended answer, and rationale; record the round; send or receive the compact acceptance `全部采用推荐选项/答案`; then resume the same Grill context and record the next frontier. A summary such as “采用默认答案” is not a Grill round. In no-confirmation mode, inject that exact acceptance into the same Grill context; do not merely claim that defaults were accepted. Do not publish any SPEC until a fresh observation proves `frontier_empty: true`.
4. Publish exactly one umbrella SPEC for the complete requirement. Use it only as a hierarchy container.
5. Partition requirements into the minimum coherent ordered child SPECs. Publish every child SPEC and set its GitHub **Parent issue** to the umbrella SPEC.
6. Before ticketing, invoke `route-codex-task` to predict and record one implementation route per child SPEC. Supply the SPEC's capability difficulty, reliability and failure-cost evidence for model choice, plus reasoning complexity, ambiguity, coupling, search space, and verification burden for independent effort choice. Record difficulty, recommendation, fallback, rationale, and any required `xhigh_evidence` in the SPEC and issue-tree record. This is a prediction only; the implementation controller performs target-host capability and applied-settings readback before dispatch.
7. Run `to-tickets` for every child SPEC. Publish vertical-slice tickets and set every ticket's GitHub **Parent issue** to its owning child SPEC. Tickets inherit the owning SPEC route; never choose a model or effort per ticket.
8. Read every Parent issue relationship back from GitHub. Body links, task lists, labels, project fields, and dependency edges are not equivalent. Repair missing or incorrect relationships before completion.
9. Write the issue-tree record in [the contract](references/contract.md), validate it with `scripts/validate_issue_tree.py`, and return the umbrella SPEC, ordered child SPECs, tickets, dependency edges, implementation routes, and validation evidence.

Apply viable defaults throughout. Ask only for objectively unavailable credentials or authority. A tracker that cannot represent GitHub Parent issue relationships is a blocker, not permission to simulate the hierarchy.

After every external operation, persist the issue-tree record and re-check the current
phase. Do not report Grill completion from a summary alone: require an empty frontier
and structured round evidence. Each round evidence entry must contain `round`, a
non-empty `questions` array, and for every question `number`, `question`,
`recommendation`, and `rationale`, plus the exact `acceptance_command`, non-empty
`acceptance_evidence`, and non-empty `resume_evidence`. The final entry must contain
`frontier_empty: true` and `frontier_empty_evidence`. Do not report SPEC/ticket completion until every
GitHub Parent issue relationship has been read back and the validator passes. If GitHub,
the tracker, or a required protocol is unavailable, return a blocker packet and use
`unblock-development`; do not simulate missing issues locally.

## Execution reliability rules

The task running this skill is the planning executor, not a monitor of another
executor. It must perform the Grill and issue publication itself. It must not create a
second planning task, wait for a total-control task, or forward the assignment and
then report the forwarded task's summary as its own evidence. The only exception is a
real environment blocker routed through `unblock-development`; that repair task may
not own the issue tree and must be archived before planning resumes.

Use a small, recoverable transaction around every GitHub operation:

```text
census existing issues -> create or reuse one item -> read it back -> persist its id
-> set/read back its Parent -> persist the relationship -> continue
```

Never batch a whole tree behind one command window. On timeout or command failure,
read the current issue census and the last persisted record first, then retry only
missing work. Match by a stable run marker/title before creating anything, so a retry
cannot create a duplicate. If a rerun has already created duplicate issues, choose a
single canonical tree, keep the history, close duplicates with a duplicate-of note,
and exclude them from the final tree; never silently delete them.

Treat an `active` task with no new tool marker or external-state change across two
bounded observations as suspected stalled execution. Send one direct resume instruction
to the same executor. If it still does not advance, open a dedicated blocker-repair
task, verify the repair, archive it, and resume the exact planning step. Do not finish
with “等待中”“正在处理” or an unchanged progress paragraph.

The final handoff is independently auditable. Before returning it, verify that the
new issue-tree file exists in the current run namespace, its issue ids are present in
the fresh GitHub census, all selected nodes have the expected labels and relationships,
and the validator result is `decision: allow`. A child final, an idle task, a local
draft, or a successful archive call without readback is not completion evidence.

## Completion gate

Finish only when Grill has an empty frontier, all artifacts are published, every child SPEC has a justified implementation route, every child SPEC points to the umbrella SPEC, every ticket points to its owning SPEC, and validation succeeds. Under `implement-needs` or another parent orchestrator, return this verified handoff to the parent instead of producing a workflow-terminal user answer. Never continue into implementation in the same task after this gate.
