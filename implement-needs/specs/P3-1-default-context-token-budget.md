# SPEC P3-1：默认阶段上下文压缩与 Token 预算

## Problem Statement

安全控制器已经具备阶段最小投影，但兼容的默认上下文接口仍可能把完整运行 snapshot、摘要字段和增量事件同时返回。随着 SPEC、Ticket、决策和证据增加，同一事实会被重复传输，Token 成本随运行增长；同时，简单删字段可能让审计信息或下一步动作所需事实不可回取。

## Solution

在不改变状态机、证据 Gate、授权范围、幂等 Intent/Action、恢复和同步语义的前提下，把默认阶段上下文改为紧凑 envelope：保留当前阶段可执行决策所需的摘要、业务版本、增量事件、未解决异常和稳定历史指针；完整 snapshot 只在显式历史读取时返回。为固定 benchmark 任务建立前后对比，只有在安全正确性、证据覆盖率和可回取性不下降时，才接受 Token/字节数下降。

## User Stories

1. As a controller worker, I want the default context to contain only current actionable facts, so that repeated historical data does not consume the working context.
2. As a recovery worker, I want omitted details reachable through stable pointers, so that compression never turns missing context into guessed state.
3. As a state writer, I want the business version and event cursor retained, so that compact context cannot authorize stale writes.
4. As an auditor, I want the full persisted snapshot preserved, so that transport compression does not delete evidence.
5. As an operator, I want an explicit history retrieval boundary, so that large history is fetched only when the current action requires it.
6. As a benchmark owner, I want fixed before/after tasks and identical safety gates, so that a smaller payload is a meaningful optimization result.
7. As a reviewer, I want correctness, evidence coverage, and pointer reachability checked beside Token estimates, so that lower cost cannot hide a safety regression.
8. As a maintainer, I want compatibility callers to receive the same required acceptance and dependency semantics, so that compression does not change completion behavior.
9. As a controller, I want unknown provider Token and fee data reported as unknown, so that missing telemetry is not counted as savings.
10. As a release owner, I want the optimization to be revertible without changing business records, so that a failed comparison can be rolled back safely.

## Implementation Decisions

- Define one compact default context envelope for the existing phase-context boundary. It retains phase, run identity, state version, event cursor, actionable acceptance/dependency summaries, unresolved exceptions, and pointers to omitted history.
- Keep the complete snapshot in the existing durable runtime snapshot store; compression changes the returned projection, not the audit record.
- Add an explicit snapshot/history pointer contract for retrieving the omitted full payload. An invalid or unreachable pointer fails closed.
- Remove duplicate full snapshot expansion from the default response. Do not remove fields required by current state transitions, evidence gates, stale-state checks, or recovery decisions.
- Compare the compact and legacy representations using deterministic payload bytes and token estimates on the fixed benchmark task set.
- Require safety equivalence before efficiency is considered: completion outcomes, false-completion rejection, side-effect boundary, evidence coverage, stale-write rejection, and pointer reachability must remain unchanged.
- Keep provider-level Token and fee values optional; absent values remain `unknown` and cannot be used as proof of improvement.
- Do not change model selection, prompts, external provider behavior, SQLite business-event semantics, or required terminal evidence.

## Testing Decisions

- Test the public context command and programmatic phase-context boundary rather than private serialization helpers alone.
- Assert the default envelope omits the full snapshot while preserving required metadata, summaries, events, exceptions, and pointers.
- Retrieve every emitted pointer and assert the original history remains reachable; malformed and stale pointers must be rejected.
- Run the existing false-completion, evidence Gate, authorization, dependency, recovery, stale-state, and synchronization tests unchanged as safety regression gates.
- Add fixed benchmark fixtures for empty run, active SPEC with dependencies, closed delivery, recovery with an unresolved exception, and release context.
- Compare payload bytes and estimated/observed Token coverage before and after. A lower estimate without complete correctness and coverage evidence is an inconclusive result, not success.
- Verify context assembly and measurement are read-only with respect to business completion and side-effect state.

## Out of Scope

- Changing any P0/P1 completion, authorization, recovery, or synchronization rule.
- Changing the underlying SQLite event log or deleting persisted snapshots.
- Prompt rewriting, model migration, provider negotiation, or claiming provider-side Token savings that cannot be observed.
- Removing the explicit history retrieval boundary or replacing pointers with inferred summaries.

## Further Notes

This SPEC is the first post-#57 optimization step. It may be accepted only if the safety invariants from #57 remain unchanged and the benchmark reports any missing measurements explicitly.
