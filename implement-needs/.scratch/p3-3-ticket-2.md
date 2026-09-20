## Parent

[#101](https://github.com/williamxhero/skills/issues/101)

## What to build

实现预算超限时的安全 fallback：优先复用 P3-2 的 base pointer/delta 表示，保留当前 acceptance、dependency blockers、unresolved exceptions、evidence coverage、business version 和 event cursor；无法证明字段完整或 pointer 可达时，返回结构化 inconclusive/blocked，不截断、不猜测、不执行外部动作。

## Acceptance criteria

- [ ] near-limit context 可以在不丢失必需字段的情况下返回。
- [ ] overflow 只允许安全 pointer/digest fallback 或结构化非成功结果。
- [ ] malformed、stale、missing、unreachable fallback pointer 全部 fail closed。
- [ ] fallback 和 overflow 路径不写入 completion、business events、measurements claiming success 或 external intents。
- [ ] 业务版本、事件游标和证据覆盖在 fallback 前后可验证一致。

## Blocked by

- [#106](https://github.com/williamxhero/skills/issues/106)
