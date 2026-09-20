## Parent

Part of #45 (SPEC P1-1: 分离依赖结构检查与运行就绪检查)

## What to build

提供公开 CLI/reconcile 接口和回归测试，证明依赖等待、阻塞、结构错误在控制器入口保持一致。

## Acceptance criteria

- CLI/API 可分别请求结构检查和当前 readiness，并返回 `ready`/`waiting`/`blocked` 或结构错误。
- reconcile 不把 waiting 提升为 repair；真正结构错误和终态不可满足依赖才进入 repair/decision。
- 覆盖两 SPEC 顺序、未知依赖、循环、跨 run、固定顺序、取消/失败前置、有效 waiver。
- 重启后结果可重算且不依赖隐式内存状态。
- 全量回归通过并记录证据。

## Blocked by

- readiness/scheduler ticket（本 SPEC 的 readiness 实现）