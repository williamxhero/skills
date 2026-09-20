## Parent

Part of #45 (SPEC P1-3: 可执行启动契约、依赖解析与公开决策接口)

## What to build

提供显式、公开的决策记录接口，并把业务外部回执与运行指标 observation 分开；决策必须带 actor、scope、rationale、source 与授权关系，指标不能伪装成 delivery evidence。

## Acceptance criteria

- [ ] CLI/API 可记录结构化 decision，并写入 decisions 与业务事件。
- [ ] decision 缺 actor/scope/rationale/source 或授权关系时拒绝。
- [ ] metric observation 不能写入 business evidence；外部 receipt 不能通过 metric observation 接口伪装。
- [ ] 回读结果能明确区分 decision、business evidence 与 observation。

## Blocked by

- #70：可重放启动契约与依赖解析
