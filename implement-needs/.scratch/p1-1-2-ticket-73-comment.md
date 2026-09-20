P1-1.2 已实现并验证。

- 提交：`082e848 feat(implement-needs): schedule dependency readiness states`
- readiness 已接入 `next_action`：`ready` 正常推进，`waiting` 返回 `wait_spec_dependency`，结构错误/不可满足前置返回 `repair_spec`。
- 已知未交付前置不再被当成 repair；取消/失败前置无 waiver 会明确 blocked；有效 waiver 可释放 readiness。
- waiting 路径不创建 repair action、不增加业务版本、不推进状态。
- 验证：专门测试 3 项通过；P0-1 runtime state 回归 5 项通过。