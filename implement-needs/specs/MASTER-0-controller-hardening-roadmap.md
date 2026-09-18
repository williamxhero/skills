# 总控 SPEC MASTER-0：控制器安全优先的开发推进路线

## Problem Statement

`implement-needs` 的优化不能按“哪个 SPEC 先完成哪个”自由并行推进。控制器当前最严重的问题不是 Token 成本，而是可能虚假完成、写入未经验证的成功状态，或在提交和发布时扩大本次运行之外的副作用范围。如果先做上下文压缩、指标优化或文档生成，可能把不安全的状态机运行得更快，却不能证明交付真实有效。

现有 12 个子 SPEC 已分别描述具体修复，但需要一个总控契约明确它们的依赖、批次、停止条件、验证证据和最终完成定义，确保开发顺序始终遵循：先消除虚假完成，再限制副作用，再补齐恢复与可运维性，最后优化 Token 和运行效率。

## Solution

以本总控 SPEC 作为 12 个子 SPEC 的交付编排和验收边界。所有子 SPEC 必须顺序推进；只有当前批次完成独立验证、回归测试、证据回读和默认分支健康检查后，才能启动下一批。总控路线分为五个阶段：

1. **基线与安全地基**：先用回归测试固定空运行完成、缺证据关闭、空 expected 复用、无回执成功和非原子回滚等缺陷，再交付 P0-4。
2. **禁止虚假完成**：交付 P0-2 和 P0-1，形成统一证据 Gate 与运行级状态机。
3. **禁止无意扩大副作用**：交付 P0-3，冻结授权范围、候选 SHA 和最终同步一致性。
4. **补齐可靠运行链**：按依赖交付 P1-3、P1-1、P1-2、P1-4、P1-5，随后交付 P1-6。
5. **最后优化与维护**：在安全不变量冻结后交付 P2-1 和 P2-2；任何效率提升都必须在安全检查不减少的前提下测量。

## User Stories

1. As a project owner, I want one master delivery order, so that child SPECs are not implemented in a safety-invalid sequence.
2. As a controller, I want a clear prerequisite graph, so that I know which merged behavior must exist before starting the next SPEC.
3. As a maintainer, I want regression tests for known bypasses before fixes, so that later changes cannot hide whether the original defects were actually closed.
4. As a reviewer, I want each phase to have entry and exit gates, so that “code merged” is not confused with “SPEC verified.”
5. As a user, I want the controller to reject false completion before optimizing Token usage, so that faster execution does not amplify unsafe behavior.
6. As a user, I want unrelated repository changes excluded before release work is optimized, so that efficiency improvements cannot broaden delivery scope.
7. As an implementer, I want one child task to own one child SPEC through merge and evidence handoff, so that ownership, tests, and rollback boundaries remain clear.
8. As a supervisor, I want child SPECs processed sequentially, so that each next SPEC starts from the independently verified predecessor merge.
9. As a recovery operator, I want a failed child SPEC to return to the same boundary or an explicitly planned repair, so that the queue cannot silently skip a failed gate.
10. As a release owner, I want candidate SHA, test evidence, deployment evidence, and synchronization evidence checked together, so that the final delivery identity is stable.
11. As a performance owner, I want Token and cost comparisons only after safety invariants are frozen, so that benchmark improvements remain meaningful.
12. As a reviewer, I want unknown metrics reported as unknown with coverage, so that missing telemetry is not interpreted as successful behavior.
13. As a maintainer, I want documentation and generated action contracts updated after the core semantics stabilize, so that derived guidance does not become a competing source of truth.
14. As a project owner, I want the final report to show every child SPEC, dependency, merge, test, checkpoint, and remaining limitation, so that the entire optimization program is auditable.

## Implementation Decisions

### 1. Child SPEC topology

The master SPEC governs these existing child SPECs:

- P0-1: 运行级状态机与完整交付终态 — Issue #41
- P0-2: 统一状态变更证据 Gate — Issue #42
- P0-3: 授权范围、候选冻结与最终同步 — Issue #43
- P0-4: 原子事务、业务版本与真正只读访问 — Issue #44
- P1-1: 依赖结构检查与运行就绪检查 — Issue #45
- P1-2: Intent、Action、回执与恢复生命周期 — Issue #46
- P1-3: 启动契约、依赖解析与公开决策接口 — Issue #47
- P1-4: 持久化线程准备与测试列车检查点 — Issue #48
- P1-5: 策略固定、迁移、备份恢复与外部对账 — Issue #53
- P1-6: 阶段最小上下文与新鲜度保护 — Issue #54
- P2-1: 单一动作契约生成文档、CLI 与检查表 — Issue #55
- P2-2: 行为安全指标与覆盖率 — Issue #56

Each child SPEC remains the implementation scope for its own behavior. The master SPEC does not duplicate their detailed implementation decisions and does not authorize implementation inside the controller/planning task.

### 2. Mandatory execution order

The implementation order is:

```text
Baseline regression tests
  → P0-4
  → P0-2
  → P0-1
  → P0-3
  → P1-3
  → P1-1
  → P1-2
  → P1-4
  → P1-5
  → P1-6
  → P2-1
  → P2-2
```

The sequence is intentionally stricter than numeric priority. P0-4 supplies atomicity and versioning for later gates. P0-2 makes completion evidence non-bypassable. P0-1 gives the gate a real run phase to protect. P0-3 then binds scope and candidate identity to the now-verifiable delivery chain. P1-3 makes the execution environment explicit before asynchronous routing and lifecycle work. P1-1 separates normal dependency waiting from repair. P1-2 connects external lifecycle and recovery. P1-4 makes routing and test checkpoints formal. P1-5 protects policy and restore boundaries. P1-6 can reduce context only after freshness and gates exist. P2-1 and P2-2 are last because they derive from stabilized contracts and measure behavior rather than define safety.

### 3. Phase entry and exit gates

**Gate A — Baseline:** add tests that reproduce the known defects without connecting to real production release targets. Entry requires a clean record of current behavior. Exit requires each bypass to fail in a named test or be explicitly accepted as a documented limitation.

**Gate B — Atomic foundation:** P0-4 must make state, evidence references, events, and business version atomic; stale writes must fail; read-only export must not create or mutate a store. No later SPEC may rely on the old write path.

**Gate C — False-completion prevention:** P0-2 and P0-1 must ensure that empty planning cannot complete, only a verified `no_change` can represent a legitimate no-op, and Action/SPEC/Ticket/thread completion requires applicable evidence and independent readback.

**Gate D — Side-effect boundary:** P0-3 must prove that normal synchronization cannot include unrelated work, and that tests, package, deployment, final sync, and final report share one candidate SHA. Candidate drift invalidates old evidence.

**Gate E — Reliable operation:** P1-1 through P1-5 must provide explicit startup dependencies, correct waiting/blocked semantics, normal and unknown external outcomes, durable bootstrap/checkpoints, and verifiable policy recovery. No child SPEC may silently bypass a failed or unresolved checkpoint.

**Gate F — Efficiency readiness:** P1-6 may start only when business-version freshness and completion gates are enforced. P2-1 may generate derived guidance only from stabilized contracts. P2-2 may compare Token, turns, latency, and cost only with the same safety checks and fixed benchmark tasks.

### 4. Child task and recovery policy

- Process one child SPEC at a time; do not overlap implementation children.
- Each child owns its SPEC through implementation, selected tests, review, merge, evidence handoff, and archival.
- The controller independently verifies merge reachability, required tests, ticket evidence, and archival readback before marking the child complete.
- A child failure returns to the same child boundary for repair or continuation. It must not be skipped by advancing the master queue.
- A defect that changes an already accepted child contract requires a repair SPEC or an explicitly scoped correction before the next dependent child begins.
- The next child starts from the independently verified predecessor merge, not merely from a child-reported commit.

### 5. Final program invariants

The program is complete only when all of these hold:

- No run can reach completed through an empty or unverified path.
- A valid no-change result is explicit, reasoned, and independently evidenced.
- Completion predicates are enforced at public state-changing boundaries.
- State, evidence, event, and version writes are atomic and stale contexts are rejected.
- Unauthorized scope expansion and candidate drift invalidate delivery evidence.
- The final candidate SHA is identical across validation, package, deployment, synchronization, and final report.
- Normal dependency waiting is not classified as repair.
- Unknown external outcomes are reconciled before retry, and recovery budgets/ownership are explicit.
- Context, documentation, and metrics optimizations do not reduce required safety checks.
- Missing observability data is reported as unknown with coverage, never as proof of safety.

## Testing Decisions

- Use the highest seam possible: public controller/CLI lifecycle tests for end-to-end gates, with focused module tests only for deterministic storage, schema, and receipt predicates.
- Maintain a fixed regression corpus for empty-run completion, incomplete Ticket evidence, empty expected reuse, unverified Action success, transaction rollback, stale writes, unrelated worktree changes, candidate drift, dependency waiting, lost external response, policy mismatch, and missing telemetry.
- After each child SPEC, run its narrow tests plus the affected cross-SPEC acceptance tests and verify the merged revision from the default branch.
- Before moving from the safety phases to efficiency phases, run the complete false-completion and side-effect boundary suite and save a baseline receipt.
- For P1-6, P2-1, and P2-2, compare against the same fixed tasks and report correctness, error-completion rate, duplicate side effects, recovery success, turns, tokens, latency, cost, and measurement coverage.
- Do not treat a child final message, a non-empty evidence field, repository equality, or a lower Token count as sufficient completion evidence by itself.
- Preserve existing lifecycle, SQLite controller, receipt, dependency, routing, recovery, context, and runtime-metric tests as prior art; extend through public behavior where possible.

## Out of Scope

- Implementing any child SPEC inside this master planning task.
- Creating a second controller Skill or replacing the existing thin-controller architecture.
- Rewriting every reference document or moving the full directory tree before the core safety gates pass.
- Enabling production deployment, new credentials, or broader repository permissions.
- Treating Token reduction, fewer controller turns, or lower cost as success when safety or completion correctness regresses.
- Guaranteeing provider-side exactly-once behavior where the external provider only supports idempotency and readback.

## Further Notes

The core objective is deliberately ordered as:

```text
不能虚假完成
  → 不能无意扩大副作用
  → 能够可靠恢复和审计
  → 在安全不变量不变的前提下更省 Token
```

The master SPEC should be closed only after every child SPEC is independently closed and the final program invariants are verified. If implementation reveals that a proposed optimization weakens a safety gate, the optimization is rejected or replanned; the security boundary is not relaxed to preserve the schedule.
