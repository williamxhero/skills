# P0-2.1: 统一可信证据 Gate 与动作契约

## Parent

SPEC P0-2: https://github.com/williamxhero/skills/issues/42

## What to build

提供一个可复用的状态变更 Gate，验证完整 expected/action contract、运行与目标身份、业务版本、执行者授权、候选 SHA、环境、来源信任和独立可信回读。空 expected、缺字段、跨运行或跨候选证据必须得到可操作的结构化拒绝原因。

## Acceptance criteria

- [ ] Gate 对完整契约返回可序列化的 allow 结果，对空/缺字段/错误类型 expected 返回明确 reject code。
- [ ] Gate 拒绝 run、target、candidate、environment、actor 或 source trust 不匹配的回执。
- [ ] Gate 不写入业务状态；其判断可被程序 API 和 CLI 复用。

## Blocked by

- SPEC P0-4 / Issue #44: 统一事务、业务版本与证据引用存储

