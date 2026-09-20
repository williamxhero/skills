## Parent

Part of #41 (SPEC P0-1: 补全运行级状态机与完整交付终态)

## What to build

让统一调度器从持久化运行阶段和阶段回执计算唯一安全动作，并在最终验证、发布、同步和终态之间执行完整门禁。没有规划回执的空 SPEC 集合必须继续等待或修复；只有显式且有证据的 no_change 才能到达 no_change，只有完成所有适用交付条件才能到达 completed。

## Acceptance criteria

- [ ] next_action 返回当前阶段允许的动作，不再从空 SPEC 集合直接返回 final_release 或成功。
- [ ] preflight、Grill、planning、implementation、final_verification、release、synchronization 的阶段回执缺失、过期或身份不匹配时 fail closed。
- [ ] 合法 no_change 必须包含明确原因与验证证据，并与 completed 区分。
- [ ] blocked 与 user_stopped 返回可恢复动作和最后安全阶段，不得被报告为成功。
- [ ] 全部 SPEC、Ticket 和发布/同步条件满足前，终态判定拒绝 completed。

## Blocked by

- #64：持久化运行级状态机与阶段回执
