P1-3.3 已实现并验证。

- 提交：`869afcc docs(implement-needs): publish startup contract reference`
- 根 `SKILL.md` 现在明确要求读取 `references/startup-and-scope.md`，并在 phase work 前记录/验证 startup contract。
- 新增 reference index：启动顺序、公开 CLI、依赖 fail-closed 错误码、decision/observation/evidence 边界。
- CLI/重启回归覆盖：缺失契约结构化 `startup_contract_missing`、未注册 lookalike 不替换、重启后各审计流保持分离。
- 验证：P1-3.3 专门测试 3 项通过；全量测试 246 项通过。