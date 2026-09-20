## Parent

Part of #43 (SPEC P0-3: 授权范围、候选冻结与最终同步一致性)

## What to build

在所有候选变更完成后冻结唯一 candidate SHA，并把测试、打包、部署和后续同步证据绑定到该候选。任何提交、合并、远端推进或生成物变化都必须使旧冻结失效，回到重新验证，而不能沿用旧完成声明。

## Acceptance criteria

- [ ] 只能在授权快照允许的范围内冻结 candidate SHA，并持久化冻结证据。
- [ ] test/package/deployment/sync 证据必须引用同一 run、candidate SHA 与授权快照。
- [ ] 候选冻结后发现 HEAD、工作区、远端或生成物变化时，旧验证被标记 invalidated 并产生重新验证动作。
- [ ] 候选身份不匹配时，即使仓库 local/remote equality 成立，也不能报告 release 或 completed。
- [ ] 稳定候选的完整证据链可以被审计重放，包含 merge SHA、artifact/deployment identity 与 candidate SHA。

## Blocked by

- #67：运行授权快照与范围门禁
