## Parent

[#100](https://github.com/williamxhero/skills/issues/100)

## What to build

把阶段化 delta 接入默认上下文命令和固定 benchmark，验证各阶段只返回当前可执行事实；对 unchanged、changed、recovery、dependency、evidence 和 policy invalidation 场景给出可复现的 legacy/P3-1/P3-2 对比和安全等价结论。

## Acceptance criteria

- [ ] 默认 context 命令可以产生版本化 delta，并能在需要时显式回取完整历史。
- [ ] 五类 P3-1 fixture 均覆盖 unchanged 与 changed 路径。
- [ ] benchmark 报告 bytes、estimated/observed tokens、correctness、evidence coverage、stale-write rejection、side-effect boundary 和 pointer reachability。
- [ ] 只有所有安全项通过且 payload/token 有真实下降时才返回 allow，否则返回 inconclusive。
- [ ] #57 全量 false-completion、evidence、authorization、recovery、synchronization 和 read-only 回归通过。

## Blocked by

- [#104](https://github.com/williamxhero/skills/issues/104)
