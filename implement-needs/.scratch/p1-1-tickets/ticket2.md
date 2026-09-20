## Parent

Part of #45 (SPEC P1-1: 分离依赖结构检查与运行就绪检查)

## What to build

实现当前候选 SPEC 的 readiness 投影，并接入调度器：`ready`、`waiting`、`blocked` 必须与结构检查分离。

## Acceptance criteria

- 已知且尚未交付的前置 SPEC 返回 `waiting`，带 blocker edge 和下一次检查目标，不返回 repair。
- 前置 SPEC 已取消/失败且没有有效 waiver 时返回 `blocked`，带可执行原因。
- 所有前置依赖已关闭时返回 `ready`。
- 调度器对 waiting 保留队列，不创建 repair action、不消耗恢复预算；blocked 不得推进目标 SPEC。
- readiness 输出可被公开状态/后续 reconcile 使用。

## Blocked by

- 结构检查 ticket（本 SPEC 的结构校验）