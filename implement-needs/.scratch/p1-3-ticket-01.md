## Parent

Part of #45 (SPEC P1-3: 可执行启动契约、依赖解析与公开决策接口)

## What to build

在 run 启动时解析并持久化可重放的 startup contract，记录 Python/runtime、Skill root、target repository、tracker mode、host capabilities、权限与依赖解析结果；缺失或不匹配时进入明确的 blocked_missing_dependency/authorization 阻塞，不猜测替代环境。

## Acceptance criteria

- [ ] startup contract 绑定 run、runtime、Skill root、target repository、tracker、host capabilities 与权限。
- [ ] 依赖记录 canonical name、显式 alias、实际路径、版本/摘要和 adapter contract。
- [ ] 缺失、错误摘要、版本不兼容或歧义依赖 fail closed，并保留结构化阻塞原因。
- [ ] 启动契约写入前不执行写副作用；后续阶段可读取并验证同一契约。

## Blocked by

- None (can start immediately)
