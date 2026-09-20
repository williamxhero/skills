P1-2.1 已实现并验证。

- 提交：`dac23c6 feat(implement-needs): enforce intent outcome reconciliation`
- 新增持久化 Operation Intent；幂等身份包含 run、logical action、target、target state，不同目标状态不会误去重。
- `succeeded` 必须同时有 verified response 与 authoritative readback；失败和 `outcome_unknown` 有明确状态。
- 未知结果在 reconcile 前不能直接 succeeded，`next_action` 优先返回 `reconcile_intent`，对账成功后不重新发送外部动作。
- CLI 已暴露 prepare-intent、intent-outcome、reconcile-intent。
- 验证：专门测试 3 项通过；全量测试 258 项通过。