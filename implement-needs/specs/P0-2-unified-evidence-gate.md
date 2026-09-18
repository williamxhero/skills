# SPEC P0-2: 统一状态变更证据 Gate 与回执适用性

## Problem Statement

公开状态变更入口目前允许调用者提交形状正确但未经验证的成功声明。Action、SPEC、Ticket 和线程的完成条件分散且不一致；空 `expected` 会跳过回执适用性匹配，提交证据和测试证据也没有始终要求同时存在。结果是系统能写入违反主流程约束的业务状态，再期待外层审计事后发现。

## Solution

建立统一的状态变更 Gate 和动作契约。所有完成、关闭、归档和成功转移必须在写入前验证运行与目标身份、授权范围、预期业务版本、当前合法状态、执行者、必要回执及回执的候选版本和环境适用性。调用者提供的成功值只作为待验证声明；最终结论由验证器根据可信回读和绑定证据计算。`expected` 必须符合完整 Schema，空对象直接拒绝。

## User Stories

1. As a controller, I want one gate for all business state transitions, so that low-level public commands cannot bypass lifecycle rules.
2. As a verifier, I want success to be computed from applicable evidence, so that a caller cannot manufacture a completed state with a non-empty string.
3. As a SPEC owner, I want closure to require the merged PR, all applicable Ticket closures, and archived-thread readback, so that SPEC completion reflects delivery rather than claims.
4. As a Ticket owner, I want both matching commit evidence and matching test evidence to be mandatory, so that one evidence category cannot substitute for the other.
5. As an Action owner, I want the target identity and candidate SHA bound to the receipt, so that evidence from another run cannot be reused.
6. As an operator, I want stale versions rejected before a write, so that an old observation cannot overwrite a newer decision.
7. As a receipt producer, I want expected fields to have a defined schema, so that omitting all constraints cannot make a receipt appear applicable.
8. As a thread supervisor, I want an archive operation and post-operation readback, so that an idle or final child is not mistaken for archived.
9. As a reviewer, I want failed gate checks to name the missing or mismatched condition, so that recovery is actionable.
10. As a maintainer, I want all gates to be reusable by CLI and programmatic callers, so that behavior does not diverge by entry point.

## Implementation Decisions

- Introduce a single gate layer that receives an action contract, current entity state, actor identity, frozen run plan, expected business version, and evidence set.
- Define separate predicates for recorded claims and verified success; only verified success may advance a terminal state.
- Require Ticket closure to contain both current-SPEC commit evidence and a test receipt applicable to the same candidate and test scope.
- Require SPEC closure to verify merged delivery, all required Ticket outcomes, and thread archival plus independent readback.
- Require Action success to be backed by a verifier-generated receipt containing target identity, candidate SHA, environment, source trust, and readback status.
- Reject `expected={}` and incomplete expected schemas at the input boundary; generate expected constraints from the frozen run plan whenever possible.
- Centralize transition definitions and gate failures so every CLI command and internal caller receives the same rejection semantics.

## Testing Decisions

- Use public commands to attempt every known bypass: missing owner, wrong identity, stale version, wrong SHA, missing commit, missing test, empty expected, untrusted source, and missing archive readback.
- Verify that each invalid transition is rejected without changing the entity or its event log.
- Verify positive closure for a fully matching candidate and evidence bundle.
- Verify that evidence from another SPEC, run, environment, or candidate is rejected.
- Test both direct CLI and controller-driven paths against the same gate contract.
- Add regression coverage for the existing “commits plus tests is non-empty” bug, proving that both collections are independently required.
- Use delivery receipt and terminal validator tests as prior art, adding end-to-end public-interface cases.

## Out of Scope

- Designing the run phase machine, which is P0-1.
- Implementing external provider fencing or retry semantics, which is P1-2.
- Changing the meaning of already valid historical receipts except through an explicit migration.

## Further Notes

The central invariant is “recording success is not verifying success.” The system may retain an auditable pending declaration, but must not change completion state until the verifier establishes applicability and trust. The gate should be the highest reusable seam, with state-specific contracts supplying only their required evidence.
