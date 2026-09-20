P1-2.2 已实现并验证。

- 提交：`b866824 feat(implement-needs): persist claims and recovery budgets`
- 新增持久化 claim/lease/fencing 与 recovery record；active lease 阻止第二 owner。
- 过期接管必须有 verified fencing capability/receipt，不能以 caller Boolean 伪造安全接管。
- 恢复预算由唯一 owner 持有；预算耗尽将原 Intent 置为 paused，不创建新 run 绕过限制。
- CLI 已暴露 claim-intent 与 record-recovery。
- 验证：专门测试 2 项通过；全量测试 260 项通过。