# SPEC P0-1: 补全运行级状态机与完整交付终态

## Problem Statement

`implement-needs` 当前可在没有完成 preflight、Grill 或规划的情况下，把空 SPEC 集合送入发布和终验。空集合同时可能表示“尚未规划”“规划失败”“合法地无需修改”，而 `all([])` 又会把它误判为完成。控制器因此不能表达真实运行阶段，也不能证明 Grill、规划、实现、最终验证、发布和同步已经依次完成。

## Solution

建立持久化的运行级状态机，明确记录阶段、阶段回执、结果和停止原因。运行必须经过 preflight、Grill、规划、实现、最终验证、发布和同步等阶段；没有 SPEC 不得默认完成。对已验证的合法无修改结果提供独立 `no_change` 终态，对阻塞和用户停止提供独立结果，并让统一调度器只返回当前阶段允许的动作。

## User Stories

1. As a需求交付发起者, I want a run to begin in an explicit initialized state, so that an empty database cannot look like a completed delivery.
2. As a controller, I want preflight, Grill, planning, implementation, final verification, release, and synchronization to be persisted stages, so that recovery can resume at the correct boundary.
3. As a planner, I want a planning receipt to prove that planning completed, so that the controller cannot skip directly to release.
4. As an implementer, I want the current stage and allowed next actions to be machine-readable, so that I do not infer lifecycle order from prose.
5. As a user whose request needs no code change, I want a verified `no_change` result with a reason and evidence, so that it is distinct from unfinished planning.
6. As an operator, I want blocked and user-stopped outcomes to retain the last safe stage and recovery action, so that an interrupted run is not reported as successful.
7. As a release verifier, I want final verification to require all applicable SPECs and checkpoints, so that a partial run cannot reach release.
8. As a maintainer, I want invalid phase transitions rejected atomically, so that a caller cannot jump over required work with a valid-looking status update.
9. As a recovery operator, I want stage receipts to be replayable, so that recovery can distinguish completed work from a stale claim.
10. As a reviewer, I want the user-facing final report to name the terminal result and evidence boundary, so that completion, no-change, and blockage are understandable.

## Implementation Decisions

- Add a persisted run phase, terminal result, transition history, and phase-specific receipt reference.
- Define the canonical flow as `initialized → preflight_passed → grilling → planning → implementing → final_verification → release → synchronization → completed`, with explicit blocked, user-stopped, and no-change outcomes.
- Make the planning receipt authoritative for whether the run has produced SPECs or a verified no-change decision.
- Make the scheduler derive actions from the current phase and pending receipt obligations; it must never use SPEC list emptiness as a completion predicate.
- Keep stage transitions and their audit events in one atomic write, carrying the current business version.
- Preserve the existing thin-controller model: semantic work is delegated, while lifecycle authority remains in the controller kernel.
- Keep directory reorganization and broad reference-document moves out of this increment; add only the minimum protocol references needed to make phase transitions discoverable.

## Testing Decisions

- Test external lifecycle behavior through the public controller and CLI entry points.
- Reproduce the empty-run path and assert that it cannot reach release or completed.
- Assert that a valid planning receipt with an explicit, evidenced no-change decision reaches `no_change` and not `completed`.
- Assert that missing, stale, or mismatched phase receipts prevent the next transition.
- Exercise restart and recovery at every phase boundary and verify that the persisted phase is the source of the next action.
- Verify that invalid jumps, duplicate transitions, blocked runs, and user stops remain auditable and cannot be reported as successful.
- Use existing lifecycle and terminal-validation tests as prior art, extending them through public APIs rather than testing private SQL helpers alone.

## Out of Scope

- Implementing the evidence gate itself beyond the phase-facing contract; that belongs to P0-2.
- Implementing repository-scope and candidate-freeze rules; that belongs to P0-3.
- Optimizing context size or runtime metrics.
- Moving every reference file or creating a new Skill.

## Further Notes

The `no_change` result is required because simply rejecting every empty SPEC list would reject a legitimate, verified outcome. The implementation sequence should first add regression tests for empty completion and only then change the state machine. The final report must distinguish completed delivery, verified no-change, blocked, and user-stopped runs.
