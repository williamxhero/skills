# SPEC P1-1: 分离依赖结构检查与运行就绪检查

## Problem Statement

依赖验证把“依赖图非法”和“合法前置任务尚未交付”混成同一类错误。一个正常的 `SPEC-1 → SPEC-2` 队列在刚建立时会被标记为 `blocker_not_delivered` 并送入 repair，导致合法等待被误判为结构损坏。

## Solution

拆分静态结构验证与当前节点就绪性评估。结构层只判断未知引用、跨运行归属、循环、固定顺序冲突和不可满足的终止条件；就绪层只评估当前候选节点并返回 `ready`、`waiting` 或 `blocked`。调度器对 `waiting` 保持队列和下一检查动作，对真正结构错误或失败前置依赖才进入修复/决策。

## User Stories

1. As a planner, I want a valid dependency graph accepted before predecessors finish, so that planning does not create a false repair.
2. As a scheduler, I want to know whether the current SPEC is ready, waiting, or blocked, so that I can choose the correct next action.
3. As a maintainer, I want unknown references and cycles reported as structural errors, so that malformed plans fail early.
4. As an operator, I want a known unfinished predecessor represented as waiting, so that I do not waste recovery budget repairing normal sequencing.
5. As a controller, I want cancelled or failed predecessors without valid waivers distinguished from ordinary waiting, so that a real decision is requested.
6. As a reviewer, I want fixed-order violations separated from delivery status, so that graph correctness is independently auditable.
7. As a recovery system, I want blocked readiness to include the blocking predecessor and reason, so that a repair action is targeted.
8. As a test author, I want the same graph to be checked statically and dynamically, so that each layer has a clear contract.

## Implementation Decisions

- Define a structural validation result independent of current delivery status.
- Define a readiness result for the current candidate node with `ready`, `waiting`, and `blocked` outcomes plus blocking edges.
- Treat known, not-yet-delivered predecessors as normal waiting and schedule re-evaluation without converting the run to repair.
- Treat missing dependencies, cycles, wrong run ownership, fixed-order violations, and terminally unsatisfiable predecessors as structural or decision blockers.
- Make reconcile consume both results without upgrading `waiting` to `repair`.
- Ensure the scheduler and user-facing status expose the distinction consistently.

## Testing Decisions

- Test a valid two-SPEC chain before and after the first SPEC closes.
- Test unknown dependency, cycle, cross-run dependency, fixed-order violation, cancelled predecessor, failed predecessor, and valid waiver independently.
- Assert `waiting` does not create a repair action or consume repair budget.
- Assert `blocked` includes actionable edges and cannot advance the dependent SPEC.
- Use dependency validation and reconcile tests as prior art, adding controller-level scheduling assertions.

## Out of Scope

- Changing how SPECs are authored or ordered.
- Implementing recovery budget ownership, which is P1-2.
- Redesigning the run state machine.

## Further Notes

The semantic distinction is: unknown dependency/cycle/order violation is an error; known unfinished predecessor is waiting; cancelled or failed predecessor without a valid waiver requires decision or repair. This distinction should be visible in both machine output and human-readable status.
