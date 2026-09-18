# SPEC P2-1: 以单一动作契约生成文档、CLI 与检查表

## Problem Statement

状态词汇、Ticket 转移、默认行为、CLI 参数、dispatch 说明和参考文档分散重复。修改时容易只更新一处，Agent 会看到互相矛盾的动作要求；未知动作还可能落入通用兜底文案，缺少权限、回执、恢复和幂等身份约束。

## Solution

建立单一动作契约定义，集中描述每个动作的输入、前置状态、权限、所需回执、允许副作用、输出状态、失败/恢复类别、幂等身份和必读参考文档。由该定义生成状态转移表、CLI 帮助、阶段检查表和 dispatch 提示；新增动作没有完整契约时拒绝注册，不使用通用兜底执行。

## User Stories

1. As a maintainer, I want each action defined once, so that state and documentation cannot drift.
2. As an agent, I want generated CLI help and phase tables, so that I can discover the exact inputs and evidence for an action.
3. As a controller, I want every action to declare allowed side effects, so that authorization can be checked before execution.
4. As a verifier, I want required receipts and applicability conditions in the contract, so that completion gates are deterministic.
5. As a recovery operator, I want failure and recovery categories declared per action, so that the correct recovery path is selected.
6. As a scheduler, I want idempotency identity declared per action, so that retries and distinct logical transitions are handled correctly.
7. As a documentation author, I want references generated from the action contract, so that required reading remains discoverable.
8. As a maintainer, I want unknown or incomplete actions rejected at registration, so that generic fallback text cannot drive execution.

## Implementation Decisions

- Define an action contract schema with name, phase, inputs, preconditions, actor/permission, evidence requirements, side effects, output state, failure classes, recovery policy, idempotency identity, and required references.
- Make transitions and CLI declarations consume the same contract rather than duplicate literals.
- Generate human-readable stage tables, command examples, and dispatch guidance from the contract where practical.
- Validate contract completeness at import/startup and reject unknown actions at scheduling time.
- Keep generated documentation clearly marked as derived, while preserving explanatory prose in reference documents.
- Add consistency checks that compare generated output with registered transitions and public commands.

## Testing Decisions

- Register valid, incomplete, duplicate, and unknown action definitions.
- Assert generated CLI/help/stage output contains the contract's inputs, gates, and recovery semantics.
- Verify scheduler behavior follows the declared preconditions and refuses undeclared side effects.
- Verify idempotency identity and required references are present for every action.
- Change one contract field in a fixture and assert consistency tests detect stale generated output.
- Use transitions, dispatch, and CLI contract tests as prior art.

## Out of Scope

- Adding new business actions solely for this documentation improvement.
- Implementing the P0 state gates themselves.
- Replacing the prose reference documentation with generated text only.

## Further Notes

This is intentionally a P2 maintainability improvement. It should consume the canonical contracts created by P0/P1 work, not become a second source of truth. It also supports the recommended gradual reference-directory reorganization.
