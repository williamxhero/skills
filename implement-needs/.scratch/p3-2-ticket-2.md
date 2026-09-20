## Parent

[#100](https://github.com/williamxhero/skills/issues/100)

## What to build

为被摘要替代的历史集合提供显式、版本绑定的读取边界。worker 可以按 pointer/cursor 取回事件、证据、决策、依赖或异常历史；缺少基线、digest 不匹配、cursor 不连续、业务版本过期或 pointer 不可达时，读取和 delta 应结构化 fail closed。

## Acceptance criteria

- [ ] 每个可省略集合都有明确的可回取 pointer 或等价读取契约。
- [ ] pointer 绑定 run、集合类型和版本/cursor，不能跨 run 或跨实体读取。
- [ ] malformed、stale、unreachable、digest mismatch 和 cursor gap 均返回稳定拒绝结果。
- [ ] 任何拒绝路径不改变 business version、events、evidence、completion 或 external intents。
- [ ] 公共 context/history CLI 和程序化边界均有回归测试。

## Blocked by

- [#102](https://github.com/williamxhero/skills/issues/102)
