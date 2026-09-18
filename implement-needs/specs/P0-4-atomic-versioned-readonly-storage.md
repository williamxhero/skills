# SPEC P0-4: 统一事务、业务版本与真正只读访问

## Problem Statement

多条业务写入路径在 autocommit 连接上使用普通连接上下文，可能先提交状态、后因事件写入失败而留下不一致账本。快照、回执关联和事件游标不能可靠表示同一次业务变化；只读导出还会创建目录、开启 WAL 或初始化空库，导致错误路径产生副作用。缺少统一业务版本也让旧上下文能够覆盖新状态。

## Solution

统一所有业务状态、回执关联、审计事件和业务版本的显式事务边界；失败全部回滚。所有状态修改携带读取时的业务版本并执行乐观并发检查，冲突返回 `stale_state`。将业务版本和纯观测指标游标分离。连接模式分为 create、open-existing 和 read-only；read-only 不创建路径、不写 PRAGMA、不建表。

## User Stories

1. As a controller, I want state, receipt references, events, and version increments to commit atomically, so that the event ledger cannot disagree with business state.
2. As a recovery operator, I want an interrupted multi-write operation to roll back completely, so that I can safely retry from a known boundary.
3. As a concurrent controller, I want stale business versions rejected, so that an old context cannot overwrite a newer decision.
4. As an auditor, I want business-version changes separated from metric observations, so that telemetry cannot masquerade as delivery progress.
5. As a read-only exporter, I want opening a missing database to fail, so that an inspection error cannot silently create an empty run.
6. As an operator, I want read-only access to avoid directory, WAL, schema, and metadata writes, so that diagnostics are genuinely non-mutating.
7. As a developer, I want one transaction helper used by every business write, so that new paths cannot accidentally rely on connection-context behavior.
8. As a receipt consumer, I want delivery proofs and dependency waivers to advance the appropriate business event/version, so that cursors represent all decision-relevant changes.
9. As a test author, I want deterministic stale-state and rollback errors, so that failure handling is explicit.

## Implementation Decisions

- Make an explicit transaction helper the only supported boundary for business mutations.
- Update entity state, related receipt/proof/waiver references, audit event, and business version within one transaction.
- Keep metrics and observability events on a separate cursor or stream that cannot satisfy lifecycle completion predicates.
- Require expected business version on every state-changing public interface; return a typed stale-state result and force context refresh.
- Implement distinct connection modes: create-if-needed, open-existing, and read-only existing. Read-only mode must reject missing paths and avoid all initialization writes.
- Add integrity checks for schema and event continuity where opening an existing store.
- Preserve existing transaction helper direction and consolidate callers rather than introducing a second transaction mechanism.

## Testing Decisions

- Inject failures between each pair of business-state, receipt, event, and version writes and assert all are rolled back.
- Run concurrent or sequential stale-version writes and assert exactly one succeeds.
- Verify metric-only observations do not advance delivery state or satisfy terminal checks.
- Verify delivery proofs and dependency waivers have the expected business event/version effects.
- Open existing, missing, corrupt, and read-only stores and assert no unexpected files or schema changes are created.
- Use the documented Python/SQLite autocommit regression as a focused test and add public controller integration coverage.
- Prioritize SQLite controller and export tests as prior art.

## Out of Scope

- Redesigning the entire event schema.
- Implementing phase semantics, which is P0-1.
- Reducing context payload size, which is P1-6.

## Further Notes

The motivating behavior has been independently reproduced with Python 3.13.5 and SQLite 3.46.1: autocommit plus `with conn:` can leave the first write committed when a later write fails, while an explicit transaction rolls both back. The fix should turn this fact into a repository regression test and use the existing transaction helper consistently.
