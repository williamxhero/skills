# SPEC P3-3：运行时上下文预算与 fail-closed fallback

## Problem Statement

即使上下文采用阶段化增量表示，异常事件、依赖链、证据摘要或恢复记录仍可能使单次 envelope 超过可控预算。若运行时静默截断，worker 可能在缺少事实时虚假完成；若无边界地继续扩张，又会使 Token 成本不可预测。

## Solution

增加只读的运行时上下文预算闸门：在返回 envelope 前计算确定性的字节数和 Token estimate，按 phase 使用明确预算，并输出覆盖率与超限原因。预算不足时按固定顺序尝试安全 fallback：返回可执行的最小必需事实加完整历史 pointer；若必需事实或 pointer 无法保证，则返回结构化 `inconclusive`/`blocked`，不推进业务版本、不执行外部动作、不伪造完成。

## User Stories

1. As an operator, I want a deterministic context budget per phase, so that context growth is observable.
2. As a worker, I want required actionable facts preserved under budget pressure, so that a smaller payload does not change decisions.
3. As a recovery worker, I want an explicit fallback pointer when history is too large, so that I can retrieve facts on demand.
4. As a safety owner, I want impossible budgets to fail closed, so that the controller cannot complete with an incomplete envelope.
5. As a metrics owner, I want estimated and observed usage distinguished, so that missing Provider telemetry remains unknown.
6. As a maintainer, I want budget measurement to be read-only, so that diagnostics cannot advance business state or create side effects.
7. As a release owner, I want overflow and fallback behavior covered by the same fixed safety benchmark, so that budget enforcement cannot hide regressions.

## Implementation Decisions

- Add budget admission to the existing context assembly/measurement boundary; do not put budget logic in business state transitions.
- Define versioned, phase-specific byte and estimated-token budgets with an explicit configuration identity.
- Required fields are determined by the executable context contract and cannot be dropped by truncation.
- Fallback may replace optional repeated collections with validated pointers/digests, but must not replace required current acceptance, dependency blockers, unresolved exceptions, evidence coverage, business version, or event cursor with guesses.
- Overflow responses are structured and non-success outcomes. They do not write completion records, business events, measurements that claim success, or external intents.
- Budget changes invalidate the relevant benchmark evidence and require a new candidate comparison.
- Provider-level observed Token, fee, and latency data remain optional and unknown when absent.

## Testing Decisions

- Test public context and measurement commands with normal, near-limit, overflow, missing-pointer, malformed-pointer, and unreachable-pointer cases.
- Assert business version, event cursor, completion status, evidence, and external intent tables are unchanged by budget evaluation and fallback.
- Reuse P3-1 fixtures and P3-2 delta cases; compare exact candidate revision and configuration identity.
- Assert overflow never returns a successful completion outcome and never silently removes a required field.
- Run the complete #57 safety regression suite and the fixed P3 benchmark before merge.

## Out of Scope

- Choosing a new model, rewriting prompts, changing Provider APIs, or claiming observed Provider savings without telemetry.
- Modifying business state transitions, evidence Gates, authorization, recovery ownership, or synchronization behavior.
- Automatic context truncation that cannot prove field coverage.

## Further Notes

P3-3 is the terminal child SPEC for the current P3 train. P3 is not complete until all fallback outcomes are explicit, safety-equivalent, and synchronized on `master`.
