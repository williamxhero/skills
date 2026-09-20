## Parent

[#101](https://github.com/williamxhero/skills/issues/101)

## What to build

建立版本化、阶段化的运行时 context budget contract，在返回 context 或 delta 前计算确定性 payload bytes 与 token estimate，并区分 observed/estimated/unknown 覆盖率。预算配置本身必须可审计且不参与业务状态推进。

## Acceptance criteria

- [ ] 每个受支持 phase 有明确的 bytes/token budget 和 configuration identity。
- [ ] budget measurement 使用确定性 canonical serialization，并同时报告 bytes、estimated tokens、observed tokens 和缺失覆盖率。
- [ ] 必需字段集合来自现有 executable context contract，不能由调用者临时删减。
- [ ] 预算评估只读，不改变 business version、events、completion、evidence 或 external intents。
- [ ] 预算配置变化会使旧 benchmark evidence 失效或要求重新比较。

## Blocked by

None (can start immediately)
