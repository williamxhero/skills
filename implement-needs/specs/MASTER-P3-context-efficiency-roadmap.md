# 总控 SPEC P3：安全不变量下的上下文效率

## Problem Statement

总控 #57 已完成控制器的虚假完成、无意扩大副作用、恢复和审计基础。P3-1 又移除了默认阶段上下文中的完整 snapshot，但当前上下文仍可能重复传输未变化的 acceptance、依赖、决策、证据和事件信息。若直接继续删字段，可能使 worker 在缺少事实时猜测状态，重新打开已关闭的安全绕过路径。

## Solution

将 P3 定义为 #57 之后、以安全不变量为前提的上下文效率列车，严格顺序推进：

```text
P3-1 默认阶段上下文压缩与 Token 预算（#94–#98，已完成）
  → P3-2 阶段化增量上下文与历史字段去重
  → P3-3 运行时上下文预算与 fail-closed fallback
```

P3-2 只压缩未变化或非当前阶段必需的事实，并为每个省略内容提供可验证的显式 pointer、digest 或 cursor。P3-3 在运行时执行预算闸门；预算不足时只能返回完整可取回的 pointer、阻塞或 inconclusive，不能静默丢弃安全字段，也不能自动推进业务状态。

## User Stories

1. As a controller worker, I want unchanged context facts represented by stable digests and cursors, so that repeated turns do not resend the same history.
2. As a recovery worker, I want every omitted fact reachable through an explicit pointer, so that compression never requires guessing.
3. As a state writer, I want the current business version and event cursor retained, so that compact context cannot authorize stale writes.
4. As an evidence reviewer, I want evidence counts, digests, and reachability checked together, so that smaller payloads do not hide missing proof.
5. As a phase owner, I want only the current phase's actionable facts in the default envelope, so that irrelevant historical data does not consume context.
6. As an operator, I want a runtime context budget, so that unbounded history cannot silently expand a controller turn.
7. As a safety owner, I want budget overflow to fail closed or require explicit history retrieval, so that efficiency cannot weaken completion gates.
8. As a benchmark owner, I want fixed P3-1 fixtures reused for every P3 comparison, so that improvements remain comparable.
9. As a maintainer, I want context reuse invalidated by business-version, event-cursor, phase, or policy changes, so that cached facts cannot become stale authority.
10. As a release owner, I want P3 completion to require the same false-completion and side-effect regression suite as #57, so that optimization remains subordinate to safety.

## Implementation Decisions

- P3-1 is historical completed scope and is not reimplemented.
- P3-2 and P3-3 run strictly sequentially; P3-3 starts only after P3-2 is merged and its fixed benchmark is green.
- No P3 child may change state-machine transitions, evidence applicability, authorization, Intent/Action semantics, recovery ownership, synchronization scope, model selection, prompts, or external Provider behavior.
- All context compression remains read-only with respect to business state and external side effects.
- A compact representation must retain or make explicitly reachable: run identity, phase, business version, event cursor, current actionable acceptance/dependency facts, evidence coverage, unresolved exceptions, and stale-write guards.
- P3-2 may add phase-aware delta envelopes, stable digests, cursors, and history pointers. Invalid, stale, or unreachable references fail closed.
- P3-3 may add deterministic budget admission, overflow classification, and safe fallback. It must never solve overflow by silently dropping required fields.
- Provider Token, fee, or latency values remain `unknown` when unavailable; estimates are never presented as observed provider savings.

## Testing Decisions

- Reuse the public controller/CLI context and history boundaries as the primary seam.
- Reuse all five P3-1 fixtures: empty run, active dependency, closed delivery, recovery exception, and release context.
- Run the complete #57 false-completion, evidence, authorization, stale-write, recovery, synchronization, and read-only regression suites after each child SPEC.
- Compare legacy, P3-1, and candidate envelopes using deterministic bytes, estimated tokens, coverage, pointer reachability, and unchanged safety outcomes.
- Test cache/delta invalidation for business-version, event-cursor, phase, policy, evidence, and unresolved-exception changes.
- Test malformed, stale, missing, and unreachable pointers as structured fail-closed outcomes.
- Test budget overflow with required fields present, explicit fallback, unchanged business version, and no external action.

## Out of Scope

- Model migration, Prompt rewriting, Provider negotiation, or claims about Provider-side Token savings that cannot be observed.
- Removing durable snapshots, events, evidence, or audit records.
- Relaxing any completion, evidence, authorization, stale-state, recovery, synchronization, or release gate.
- Parallel implementation of P3 children.

## Further Notes

P3 is complete only when P3-1, P3-2, and P3-3 are independently merged, their tickets and evidence are closed, the fixed benchmark remains safety-equivalent, the full regression suite passes, and `master` is synchronized with `origin/master`.
