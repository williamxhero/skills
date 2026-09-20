P1-4.3 已实现并验证。

- 提交：`5b377a4 test(implement-needs): verify bootstrap checkpoint recovery`
- `next_action` 为未完成子线程 bootstrap 返回唯一的 `verify_thread_route`/`assign_thread` 动作；控制器线程不抢占子 SPEC 调度。
- CLI/重启回归覆盖 bootstrap-state、initialize-test-train、持久化 route 状态和 test train。
- 既有 reconcile 恢复路径保持 controller identity/route/ticket action 的原有顺序。
- 验证：reconcile 回归 10 项通过；全量测试 267 项通过。