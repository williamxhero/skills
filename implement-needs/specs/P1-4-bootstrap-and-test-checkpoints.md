# SPEC P1-4: 持久化线程准备与测试列车检查点

## Problem Statement

线程 bootstrap、路由验证和任务分配目前受“同一 controller turn”描述约束，但宿主是异步的；主流程也只在末尾调用测试列车，容易漏掉每个 SPEC 的 L0–L2、条件 L3 和固定周期的 L4 检查点。线程最终消息、空闲状态和归档状态之间没有稳定的持久化边界。

## Solution

以持久化 `bootstrap → route_verifying → assigned` 状态替代同 turn 约束。只有 route receipt 验证通过才能发送分配；在预算内修复同一 bootstrap，明确取消时才归档并做回读。规划阶段初始化测试列车，为每个 SPEC 和检查点建立正式动作，检查点通过前阻止后续 SPEC，最终发布仅作为额外终验而非补做中间检查。

## User Stories

1. As a controller, I want bootstrap state persisted across turns, so that asynchronous host timing does not invalidate a valid assignment.
2. As a supervisor, I want route verification before assignment, so that a child cannot receive work under an unverified model/effort route.
3. As a recovery operator, I want bootstrap repair to reuse the same child identity within budget, so that duplicate implementation tasks are not created.
4. As a user, I want explicit cancellation to archive the child and read back archival state, so that a cancelled task is not left ambiguous.
5. As a planner, I want the test train initialized with every SPEC obligation, so that tests are scheduled rather than remembered.
6. As a controller, I want each SPEC's L0–L2 and conditional L3 obligations represented as blocking actions, so that implementation cannot skip them.
7. As a release owner, I want periodic L4 checkpoints to stop the next segment until they pass, so that drift is detected before final release.
8. As an operator, I want child idle/not-loaded/final states treated as unarchived until readback, so that task lifecycle is accurate.
9. As an auditor, I want route, assignment, checkpoint, verification, and archive receipts persisted separately, so that each boundary is independently proved.

## Implementation Decisions

- Add durable child lifecycle states and timestamps for bootstrap, route verification, assignment, running, handoff, archive requested, archived, and archive-readback confirmed.
- Require a route receipt with applied model/effort and owner identity before assignment; retain the same child boundary during repair.
- Replace same-turn language with persisted budgets and explicit timeout/cancellation transitions.
- Initialize a test train during planning, including per-SPEC test obligations, conditional public-contract checks, periodic owner checkpoints, and final verification.
- Expose checkpoint actions through the scheduler and block the next SPEC when a due checkpoint is unresolved.
- Record child final output as a handoff claim only; independent verification and archival remain separate transitions.

## Testing Decisions

- Simulate delayed route readback across multiple controller turns and assert no premature assignment.
- Test bootstrap repair, timeout, explicit cancellation, archive failure, and successful archive readback.
- Verify duplicate child creation is rejected when the persisted child still exists.
- Build a multi-SPEC test train and assert per-SPEC and due periodic checkpoints block progression until verified.
- Verify a final test pass cannot compensate for a missed intermediate checkpoint.
- Use task lifecycle, routing, and test-release-train tests as prior art.

## Out of Scope

- Implementing model-routing policy itself.
- Changing the underlying host's task API.
- Adding new test frameworks unrelated to the test-train contract.

## Further Notes

The controller status shown at SPEC entry and recovery must use actual applied model, thinking, and route values from readback, never requested placeholders. The child final is an event, not a completion state.
