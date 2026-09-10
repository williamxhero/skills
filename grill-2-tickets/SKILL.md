---
name: grill-2-tickets
description: Autonomously turn a software requirement into published GitHub planning artifacts by visibly default-answering Grill, creating one umbrella SPEC, partitioning child SPECs, creating tickets, and verifying Parent issue relationships. Use for “Grill 2 Tickets”, default planning through tickets, or an orchestrator's no-confirmation planning phase.
---

# Grill 2 Tickets

Turn a requirement into an approved, published, hierarchy-verified GitHub issue tree. Stop after tickets and the planning handoff; do not implement, merge, package, or deploy.

## Workflow

1. Resolve and read the installed `grilling`, `to-spec`, and `to-tickets` protocols. Treat invocation as standing authorization to accept recommended defaults and publication confirmations.
2. Run Grill to an empty question frontier. In a standalone invocation, show every numbered question, recommended answer, and rationale once, then accept them without waiting. Under a visible planning task owned by an orchestrator, keep the full frontier in that planning task; require the controller to reply and resume with only `全部采用推荐选项/答案`. Preserve visible rounds as evidence without duplicating their text across tasks.
3. Publish exactly one umbrella SPEC for the complete requirement. Use it only as a hierarchy container.
4. Partition requirements into the minimum coherent ordered child SPECs. Publish every child SPEC and set its GitHub **Parent issue** to the umbrella SPEC.
5. Run `to-tickets` for every child SPEC. Publish vertical-slice tickets and set every ticket's GitHub **Parent issue** to its owning child SPEC.
6. Read every Parent issue relationship back from GitHub. Body links, task lists, labels, project fields, and dependency edges are not equivalent. Repair missing or incorrect relationships before completion.
7. Write the issue-tree record in [the contract](references/contract.md), validate it with `scripts/validate_issue_tree.py`, and return the umbrella SPEC, ordered child SPECs, tickets, dependency edges, and validation evidence.

Apply viable defaults throughout. Ask only for objectively unavailable credentials or authority. A tracker that cannot represent GitHub Parent issue relationships is a blocker, not permission to simulate the hierarchy.

## Completion gate

Finish only when Grill has an empty frontier, all artifacts are published, every child SPEC points to the umbrella SPEC, every ticket points to its owning SPEC, and validation succeeds. Under `implement-needs` or another parent orchestrator, return this verified handoff to the parent instead of producing a workflow-terminal user answer.
