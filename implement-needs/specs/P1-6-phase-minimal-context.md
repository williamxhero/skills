# SPEC P1-6: 真正实现阶段最小上下文与新鲜度保护

## Problem Statement

阶段上下文目前主要回传一个 phase 标签，同时重复展开完整 snapshot、acceptance、decisions 等内容。上下文会随运行增长，历史全文被反复传输；expected cursor/version 又不是所有状态修改的必需输入，因此旧上下文仍可能驱动新状态。

## Solution

按 phase 和当前实体 ID 生成最小投影。实现阶段只提供当前 SPEC、直接依赖接口、当前决策所需证据摘要和必要工具输出；已关闭 SPEC 只保留交付摘要与引用指针，历史全文按需读取。上下文携带读取时的业务版本，任何写入都必须校验版本并在过期时刷新，不得同时返回完整 snapshot 和重复顶层字段。

## User Stories

1. As an implementation worker, I want only the current SPEC and direct dependency interface, so that irrelevant history does not obscure the task.
2. As a controller, I want phase-specific context projections, so that each phase receives exactly the information needed for its actions.
3. As a reviewer, I want closed work represented by summaries and pointers, so that evidence remains reachable without duplicating full text.
4. As a state writer, I want the context's business version required, so that stale reasoning cannot overwrite new state.
5. As a recovery worker, I want old context rejected with a refresh instruction, so that I can re-read current facts before acting.
6. As a user, I want token and latency measurements for the projection, so that optimization claims are evidence-based.
7. As a maintainer, I want one projection contract rather than ad hoc field deletion, so that future phases do not reintroduce duplication.
8. As an auditor, I want every omitted historical field recoverable by pointer, so that compact context does not lose traceability.

## Implementation Decisions

- Define phase/entity projection schemas for startup, planning, implementation, verification, recovery, and release.
- Make current entity ID and read business version mandatory context metadata.
- For active SPECs include acceptance, direct dependency interfaces, applicable decisions, and evidence summaries; for closed SPECs include delivery summary and stable pointers.
- Remove duplicate full snapshot/top-level expansions from default context; provide explicit on-demand historical reads.
- Enforce expected-version checks on every context-driven state mutation and return `stale_state` on mismatch.
- Instrument payload size, token estimate, latency, and refresh/rejection counts while preserving the same correctness gates.

## Testing Decisions

- Compare each phase projection against its schema and assert absent unrelated fields.
- Verify historical pointers retrieve the original content and summaries remain sufficient for the next action.
- Mutate state after context assembly and assert stale writes are rejected.
- Test active and closed SPEC projections, dependency chains, recovery context, and release context.
- Measure a fixed representative run before and after projection; report token/latency changes and correctness results.
- Use existing context and snapshot tests as prior art, adding public stale-version cases.

## Out of Scope

- Changing the underlying business event model beyond version checks.
- Replacing the task host or model context window.
- Claiming optimization success without a fixed benchmark task set.

## Further Notes

The safety requirement takes precedence over token reduction: an omitted field must be retrievable by a stable pointer, and a smaller context must never weaken the gate. This SPEC should follow the transaction/version foundation from P0-4.
