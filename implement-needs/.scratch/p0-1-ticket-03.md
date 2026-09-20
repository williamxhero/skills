## Parent

Part of #41 (SPEC P0-1: 补全运行级状态机与完整交付终态)

## What to build

通过公共 CLI 和重启/恢复路径验证运行级状态机的外部行为，覆盖空运行、合法 no_change、缺失或陈旧回执、非法跳转、阻塞、用户停止和完整交付终态，确保控制器对调用者不会给出虚假的完成结论。

## Acceptance criteria

- [ ] 公共 CLI 可创建运行、推进阶段、记录回执、查询当前阶段和终态结果。
- [ ] 空 SPEC 运行无法进入 release 或 completed；合法 no_change 可以进入独立 no_change 终态。
- [ ] 每个阶段边界重启后都从数据库恢复同一阶段、回执和下一动作。
- [ ] 缺失、陈旧、错 run/phase 的回执以及非法跳转均返回结构化拒绝并保持状态不变。
- [ ] blocked、user_stopped、completed、no_change 的用户可见报告明确说明结果、原因和证据边界。

## Blocked by

- #65：阶段驱动的安全调度与终态判定
