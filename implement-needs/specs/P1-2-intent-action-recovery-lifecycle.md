# SPEC P1-2: 贯通 Intent、Action、回执与恢复生命周期

## Problem Statement

Operation Intent 有未知结果路径，却缺少正常 `executing → succeeded` 完成入口；普通 observation 不会投影为 Action/Intent 完成，调度器也不一定优先处理恢复。Action 幂等键无法区分不同逻辑状态推进，claim、lease 和 fencing 又没有与真实外部执行能力绑定。失败分类、预算和归档确认主要依赖调用者声明。

## Solution

建立从 Intent 准备、claim、执行、成功回读、未知结果、失败到恢复的统一生命周期。每个逻辑动作和目标版本拥有稳定身份；网络重试复用同一身份，不同状态推进使用不同版本。成功必须由响应与权威回读共同确认；未知结果只能先对账，不能盲目重发。恢复预算由一个权威所有者管理，归档和 fencing 必须有真实回执/能力证明。

## User Stories

1. As an external-action owner, I want normal success to close the same Intent, so that successful work does not remain executing.
2. As a controller, I want failed and unknown outcomes projected into Action state, so that recovery becomes the next scheduled action.
3. As a retrying worker, I want network retries to reuse the original idempotency identity, so that a lost response does not create a duplicate external effect.
4. As a scheduler, I want unresolved unknown outcomes prioritized, so that new work does not bypass an unsafe external state.
5. As a concurrent worker, I want claims and leases tied to the actual executor, so that ownership is auditable.
6. As an operator, I want fencing limitations to be explicit, so that a Boolean flag cannot pretend to stop an old executor.
7. As a recovery owner, I want one authoritative budget and classification table, so that permission errors, unknown results, and transient failures do not receive unsafe identical retries.
8. As a supervisor, I want archive confirmation read back from the task host, so that a final message is not mistaken for archival.
9. As a maintainer, I want recovery to resume the original run and Intent, so that creating a new run cannot evade idempotency or budget limits.
10. As an auditor, I want each transition to reference its prior attempt and evidence, so that the lifecycle can be reconstructed.

## Implementation Decisions

- Define canonical Intent/Action states and transitions, including prepared, executing, succeeded, failed, outcome_unknown, paused, and recovery-required states.
- Add a verified success transition requiring a successful response and authoritative readback; reserve `outcome_unknown` for response loss or inability to confirm.
- Project every external receipt into the corresponding Action and scheduling view.
- Define idempotency identity as run, logical action/version, target identity, and intended target state; preserve it across network retries.
- Bind claim owner, lease, and fencing evidence to the execution adapter. Unsupported fencing must block unsafe takeover rather than accept a caller Boolean.
- Make recovery classification, budget, and archive evidence owned by one persisted recovery record.
- Require reconciliation before retrying unknown outcomes; if reconciliation says succeeded, record success without sending again.

## Testing Decisions

- Test successful response plus readback, failed response, lost response, contradictory readback, and unresolved unknown outcome.
- Verify unknown outcomes never send a second request before reconciliation.
- Compete two workers for one action and test expiry, takeover, supported fencing, and unsupported fencing.
- Verify distinct logical target states are not incorrectly deduplicated while network retries remain idempotent.
- Exhaust recovery budget and assert explicit pause/blocked outcomes and no hidden new-run escape.
- Test archive success, archive failure, and archive claim without readback.
- Use existing recovery, resource coordinator, intent, and receipt tests as prior art.

## Out of Scope

- Adding provider-specific exactly-once guarantees that the external service does not support.
- Repository candidate freezing, which is P0-3.
- Dependency readiness classification, which is P1-1.

## Further Notes

The system must not claim end-to-end exactly-once when the provider only offers idempotency and readback. The safe contract is at-most-one accepted request per logical identity when the provider honors the key, plus reconciliation before retry after an unknown result.
