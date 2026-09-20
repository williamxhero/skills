P1-4.1 已实现并验证。

- 提交：`e212a41 feat(implement-needs): persist thread bootstrap lifecycle`
- 新增持久化 bootstrap 状态：`bootstrap → route_verifying → assigned`，并记录预算、attempt、route/assignment/cancellation receipt。
- 未有 verified route receipt 前不能进入 assigned；route 延迟/失败可在同一 bootstrap 边界内重试，不能创建替代线程绕过身份。
- 显式取消写入独立 cancellation receipt；重启后状态可读回。
- 验证：专门测试 3 项通过；全量测试 264 项通过。