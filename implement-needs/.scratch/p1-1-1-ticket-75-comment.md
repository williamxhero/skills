P1-1.1 已实现并验证。

- 提交：`b063c2d feat(implement-needs): separate dependency graph validation`
- 新增独立结构验证层：未知依赖、重复节点、跨 run、循环、固定顺序冲突分别返回结构化错误。
- 结构验证完全不读取交付状态；已知但未关闭的前置 SPEC 仍是合法图，不会产生 `blocker_not_delivered` repair 错误。
- 新增 readiness 纯函数基础：为后续 ticket 提供 `ready`/`waiting`/`blocked` 投影与 blocker edges。
- 失败路径不写数据库状态、事件或恢复动作。
- 验证：专门测试 3 项通过。