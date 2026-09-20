## Parent

Part of #41 (SPEC P0-1: 补全运行级状态机与完整交付终态)

## What to build

为每个运行建立可恢复、可审计的运行级阶段状态机，持久化当前阶段、终态结果、停止原因与阶段回执，并以单事务记录阶段变化及业务事件。运行必须从 initialized 按合法顺序推进，禁止跳阶段或将空集合解释为完成；同时支持 blocked、user_stopped 与 verified no_change 的明确结果。

## Acceptance criteria

- [ ] 新建运行进入明确的 initialized 阶段，不再仅以 active 表示生命周期。
- [ ] 阶段只允许按 canonical flow 推进：initialized → preflight_passed → grilling → planning → implementing → final_verification → release → synchronization → completed。
- [ ] 非法跳转、重复终态、缺失或过期阶段回执被拒绝，且状态、事件、业务版本保持不变。
- [ ] blocked、user_stopped、no_change 的结果、原因和证据边界被持久化，并可在重启后读取。
- [ ] 阶段变更与审计事件在同一事务中提交或全部回滚。

## Blocked by

- None (can start immediately)
