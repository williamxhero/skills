P1-2 已完成，三个子 ticket 均已关闭：#76、#77、#78。

交付提交：
- `dac23c6`：Intent 正常成功/失败/未知结果生命周期，verified response + authoritative readback，未知结果必须 reconcile。
- `b866824`：幂等身份、claim/lease/fencing、恢复 owner 与预算耗尽 paused 边界。
- `29ea50c`：公开 CLI、unknown 优先调度、重启后 Intent/claim/recovery 审计回归。

验证：全量测试 261 项通过。下一总控顺序为 P1-4（#48）。