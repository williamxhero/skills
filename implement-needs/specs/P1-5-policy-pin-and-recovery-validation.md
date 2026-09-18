# SPEC P1-5: 固定策略、迁移、备份恢复与外部对账

## Problem Statement

策略 pin 目前可被不同内容覆盖，保存版本标签不等于执行时加载了同一实现。shadow 复用当前调度器也不能证明旧策略重放，数据库备份存在更不代表外部世界已回退。恢复后如果直接重放未决副作用，可能重复执行已经在备份之后成功的外部操作。

## Solution

将策略 pin 变为不可变的运行身份：同内容重复 pin 幂等，不同内容必须显式迁移并提供兼容性证据、授权来源和回退方案。执行与 shadow 都校验固定摘要和实际加载实现。增加备份恢复验收，验证文件摘要、数据库完整性、Schema 兼容性、证据可达性，并在恢复后先对账备份后可能发生的外部动作，再决定是否恢复。

## User Stories

1. As a run owner, I want one immutable policy identity per run, so that behavior cannot drift silently.
2. As a controller, I want repeated pinning of identical content to be idempotent, so that retries are safe.
3. As an operator, I want a different policy pin rejected unless a migration is explicit, so that a label update cannot change execution semantics.
4. As a reviewer, I want migration compatibility, authorization, and rollback evidence, so that policy changes are auditable.
5. As a shadow verifier, I want the loaded implementation digest checked against the pinned identity, so that shadow results actually represent the frozen policy.
6. As a recovery operator, I want backup file and database integrity validated, so that corrupt state is not restored as authoritative.
7. As a recovery operator, I want Schema and evidence compatibility checked, so that recovered decisions remain interpretable.
8. As an external-action owner, I want unresolved actions reconciled after restore, so that backup recovery never assumes the outside world rolled back.
9. As a user, I want restoration to block before side effects when post-backup activity cannot be accounted for, so that safety wins over apparent availability.

## Implementation Decisions

- Store policy identity as immutable content digest plus canonical metadata and loading implementation digest.
- Make identical pin idempotent; reject conflicting pin for an active run unless an explicit migration transition is authorized and recorded.
- Require execution and shadow entry points to verify the pinned digest and loaded implementation before producing decisions.
- Define backup manifest, database integrity, schema compatibility, evidence reachability, and policy identity checks as a recovery validation contract.
- Add post-restore reconciliation for all unresolved external Intents and operations that may have occurred after backup creation.
- Keep restore and external rollback conceptually separate; restore may recover local knowledge but cannot undo external effects.

## Testing Decisions

- Pin identical policy twice and assert no semantic change; pin different content and assert rejection.
- Test authorized migration with valid compatibility/rollback evidence and reject incomplete migration packets.
- Tamper with policy files, manifests, database, schema, and evidence references and assert recovery blocks.
- Verify shadow and execution reject a loaded implementation whose digest differs from the pin.
- Simulate an external action succeeding after backup and assert restore reconciles it before any retry.
- Use recovery validation and policy pin tests as prior art, adding end-to-end restore fixtures.

## Out of Scope

- Designing a new policy language.
- Providing external provider rollback guarantees.
- Rewriting all legacy state formats in one change.

## Further Notes

The key distinction is local restoration versus external rollback. A valid backup can restore the controller's record, but the controller must first learn what happened in the external world since the backup before resuming side effects.
