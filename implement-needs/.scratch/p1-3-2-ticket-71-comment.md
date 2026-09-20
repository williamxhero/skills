P1-3.2 已实现并验证。

- 提交：`4fcc6be feat(implement-needs): expose audited decision contract`
- 新增公开 `decide` CLI/API：decision 持久化 actor、scope、source、authorization digest，并写入 `decisions` 与 `controller_approved` 业务事件。
- 缺少 actor/scope/rationale/source/evidence/授权关系，或授权摘要不匹配时 fail closed，且不推进业务版本/事件。
- `record-observation` 继续是独立指标通道；receipt、delivery proof 等业务证据不能通过 observation 写入；正式 receipt 仍进入 evidence_refs。
- snapshot 明确分开 decisions、observations、evidence_refs。
- 验证：P1-3.2 专门测试 4 项通过；旧 sqlite controller 测试 18 项通过；全量测试 243 项通过。