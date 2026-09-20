## Parent

Part of #45 (SPEC P1-3: 可执行启动契约、依赖解析与公开决策接口)

## What to build

补齐公开启动命令、阶段—参考文档索引和最小命令示例，并通过 CLI/重启回归证明 startup contract 是后续阶段前置条件、canonical alias 只能显式匹配、错误环境不会被同名依赖替换。

## Acceptance criteria

- [ ] 公共 CLI 可初始化、读取、验证 startup contract，并返回结构化 blocked_missing_dependency/authorization。
- [ ] canonical name、注册 alias 与实际路径/摘要一致时允许；未注册 lookalike 拒绝。
- [ ] 新会话可从 root Skill 找到各阶段必读 reference index 与命令示例。
- [ ] 重启后 startup contract、decision、observation 与 business evidence 保持分离且可审计。

## Blocked by

- #71：公开决策接口与 receipt/observation 契约分离
