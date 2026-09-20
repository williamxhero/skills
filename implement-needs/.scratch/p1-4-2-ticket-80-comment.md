P1-4.2 已实现并验证。

- 提交：`55e417d feat(implement-needs): persist release train checkpoints`
- 新增 test train：每个 SPEC 的 L0/L1/L2 义务（L3 可按需加入）和固定 `checkpoint_size=10` 的 checkpoint membership。
- checkpoint 到期且未通过时，`next_action` 返回 `run_checkpoint`，后续段不能开始；最终 gate 不会替代遗漏的中间 checkpoint。
- checkpoint 记录 candidate SHA、状态和证据，支持重启后读取。
- 验证：专门测试 2 项通过；全量测试 266 项通过。