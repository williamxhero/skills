## Parent

Part of #45 (SPEC P1-1: 分离依赖结构检查与运行就绪检查)

## What to build

新增独立的依赖结构检查入口，验证 SPEC 依赖图的静态合法性，不把前置 SPEC 尚未交付误报为 repair。

## Acceptance criteria

- 检查未知依赖引用、跨 run 归属、循环和固定顺序冲突。
- 结构检查不读取交付状态作为结构错误；已知但未关闭的前置任务不得产生 `blocker_not_delivered` 结构错误。
- 返回结构化结果，区分 structural errors 与合法图。
- 未知引用、循环、跨 run、固定顺序冲突均有确定错误码和定位边。
- 失败不写业务状态、事件或恢复动作。

## Blocked by

- None (can start immediately)