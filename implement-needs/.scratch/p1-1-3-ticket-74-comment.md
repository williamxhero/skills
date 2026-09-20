P1-1.3 已实现并验证。

- 提交：`f70b4a2 feat(implement-needs): expose dependency readiness checks`
- 新增公开 `dependency-check` 与 `dependency-readiness` CLI；支持重启后从 SQLite 重算。
- reconcile 同时消费结构与 readiness：合法 waiting 返回 allow/waiting，不升级为 repair；结构错误返回 repair；不可满足前置返回 blocked/repair。
- 覆盖两 SPEC 顺序、未知依赖、取消前置、有效 waiver、CLI 读回与无写副作用。
- 验证：专门测试 3 项通过；reconcile 回归 10 项通过；全量测试 255 项通过。