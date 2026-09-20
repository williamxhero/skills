P1-5.1 已实现并验证。

- 提交：`c6978b4 feat(implement-needs): pin immutable policy identity`
- 持久化 canonical policy digest 与 implementation digest；相同 pin 幂等。
- 不同 policy/implementation 默认拒绝，只有同时提供 compatibility、authorization、rollback evidence 才能显式迁移。
- `verify-policy` 对执行/ shadow 加载的 policy 与 implementation digest 做 fail-closed 校验。
- 验证：专门测试 2 项通过；全量测试 269 项通过。