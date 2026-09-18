# SPEC P1-3: 可执行启动契约、依赖解析与公开决策接口

## Problem Statement

首次启动依赖 Agent 猜测 Skill 根目录、目标仓库、tracker 模式、宿主能力和依赖版本。协议参考文档不总是从主入口可发现，canonical name 与实际 `IN:` Skill 名称存在别名问题。必须记录的决策没有明确公开命令，外部回执和指标 observation 又共用入口，容易产生错误记录。

## Solution

增加 preflight/startup contract：解析并固定环境、仓库、权限、tracker、宿主能力、依赖 canonical name、实际路径、版本/摘要和调用适配器。缺失或不匹配时进入明确的 `blocked_missing_dependency` 或授权阻塞，不猜测同名替代品。增加显式决策记录命令，并把业务回执和运行指标分成不同输入契约。补充阶段—参考文档索引与最小命令示例。

## User Stories

1. As a user, I want a clear trigger and non-trigger contract, so that an analysis-only request does not start a write-capable delivery run.
2. As a controller, I want startup to validate Python/runtime, Skill root, target repository, tracker mode, host capabilities, and dependency versions, so that execution starts from known premises.
3. As an operator, I want canonical dependency identity, path, and digest persisted, so that another same-named Skill cannot be silently substituted.
4. As a controller, I want missing dependencies to enter a specific blocked state, so that I can report the exact preflight failure.
5. As a maintainer, I want the root Skill to distinguish its scripts from target-repository scripts, so that commands run against the intended code.
6. As an agent, I want an explicit decision command, so that I do not write SQL to satisfy an audit requirement.
7. As a verifier, I want external receipts and metrics to use separate contracts, so that a metric observation cannot be mistaken for delivery evidence.
8. As a maintainer, I want canonical names and explicit aliases validated, so that routing does not rely on fuzzy matching.
9. As a new session, I want a reference index showing what must be read at each phase, so that protocol authority is discoverable.

## Implementation Decisions

- Define startup inputs and outputs for runtime version, Skill installation root, target repository identity, tracker mode, host/task capabilities, permissions, and dependency identities.
- Resolve dependencies by canonical name plus explicit aliases, actual path, version or content digest, and adapter contract.
- Persist the resolved startup contract and use it as a prerequisite for later phases.
- Add a public decision-record command with actor, scope, rationale, source, and authorization relationship.
- Separate external delivery receipts from runtime metric observations at both schema and CLI levels.
- Add a phase/reference index that links startup, external actions, delivery evidence, recovery, context, and policy documents.
- Keep legacy names only as explicit compatibility aliases; do not allow fuzzy equivalence.

## Testing Decisions

- Test valid and invalid runtime, repository, tracker, host, and dependency inputs.
- Verify missing, wrong-digest, ambiguous-name, and incompatible-version dependencies produce the correct block without execution.
- Verify canonical alias resolution for the existing `IN:` names and reject unregistered lookalikes.
- Exercise the public decision command and confirm it creates a decision record, not an observation metric.
- Attempt to submit a metric as an external receipt and an external receipt as a metric; both must be rejected.
- Start a fresh session from the root Skill and verify each phase's required reference is discoverable.
- Use controller init, CLI contract, and routing tests as prior art.

## Out of Scope

- Changing the behavior of each dependent Skill.
- Implementing a new tracker backend.
- Performing broad documentation relocation beyond the reference index and authoritative-link cleanup.

## Further Notes

This SPEC turns implicit environment assumptions into a persisted startup contract. It should be implemented before relying on new phase actions, because later receipts need stable dependency and host identities.
