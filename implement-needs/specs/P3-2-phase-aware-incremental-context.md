# SPEC P3-2：阶段化增量上下文与历史字段去重

## Problem Statement

P3-1 已从默认阶段上下文中移除完整 snapshot，但每次传输仍可能重复发送未变化的 acceptance、依赖、决策、证据和事件信息。随着运行时间增长，这些重复内容继续消耗 Token；直接删除它们又可能让 worker 缺少完成判断、证据覆盖或恢复所需事实。

## Solution

为默认阶段上下文增加安全的阶段化增量表示：按 phase 只保留当前动作所需事实；对未变化的稳定集合返回 digest、count、cursor 和显式 history pointer；对发生变化的集合返回受边界约束的 delta。消费者遇到缺失、digest 不匹配、cursor 不连续、pointer 无法回取或版本过期时，必须 fail closed 并请求显式历史读取或刷新，不得使用猜测值。

## User Stories

1. As a worker, I want unchanged facts represented by a digest and cursor, so that repeated context turns stay bounded.
2. As an implementation worker, I want active SPEC acceptance and direct dependencies preserved, so that current actions remain executable.
3. As a recovery worker, I want unresolved exceptions and new events preserved in the delta, so that recovery decisions do not use stale history.
4. As an auditor, I want omitted evidence and historical events reachable by pointer, so that transport compression does not delete proof.
5. As a state writer, I want phase, business version, and event cursor retained, so that stale writes remain rejectable.
6. As a controller, I want phase-specific field selection, so that release-only or recovery-only history is not sent during planning.
7. As a maintainer, I want a compatibility envelope for callers that still expect the P3-1 fields, so that migration does not silently change completion behavior.
8. As a reviewer, I want malformed, stale, and unreachable deltas rejected, so that invalid compression cannot become authority.

## Implementation Decisions

- Extend the existing phase-context boundary instead of creating a second controller API.
- Keep the P3-1 envelope as the compatibility baseline; add an explicit delta mode or versioned envelope rather than silently changing field meaning.
- Derive phase field sets from one executable contract. No caller may invent its own list of required facts.
- Use deterministic canonical serialization for digests and compare the expected base cursor/version before applying a delta.
- Preserve full durable snapshots, events, evidence, and unresolved-exception records.
- Expose explicit history retrieval for each omitted collection. A pointer must identify its run, collection, base/version boundary, and remain validated against the current store.
- A delta with a missing base, mismatched digest, skipped cursor, stale business version, or unreachable pointer returns a structured rejection and no state mutation.
- Do not change external actions, model routing, prompts, state transitions, or evidence Gate semantics.

## Testing Decisions

- Test through `context`, `context-history`, and any public delta/retrieval CLI rather than only private serializers.
- Reuse the five P3-1 fixtures and add state changes for dependency, decision, evidence, event, exception, phase, and policy invalidation.
- Assert unchanged collections produce stable digests and changed collections produce complete deltas.
- Assert every omitted collection is reachable and every malformed/stale/unreachable pointer fails closed.
- Run unchanged false-completion, evidence, authorization, stale-write, recovery, synchronization, and read-only tests.
- Compare legacy, P3-1, and P3-2 deterministic payload bytes and token estimates only after correctness and coverage gates pass.

## Out of Scope

- Runtime budget admission and overflow fallback; those belong to P3-3.
- Provider-side Token accounting, Prompt changes, model changes, or external action batching.
- Deleting or rewriting durable audit records.

## Further Notes

P3-2 is accepted only when its candidate envelope is smaller on the fixed fixture set without reducing completion correctness, evidence coverage, stale-write rejection, recovery facts, or pointer reachability.
