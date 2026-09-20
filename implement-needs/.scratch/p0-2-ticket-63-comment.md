已完成 P0-2 / #63：公共 CLI 与控制器路径的证据 Gate 回归。

实现提交：`dde45bef7747e2209f50b15a5b174da030ca1211`

验证：
- `python -m unittest discover -s tests -v`：217 passed / OK
- P0-2 专项：`python -m unittest tests.test_p0_2_evidence_gate -v`：6 passed / OK
- `git diff --check`：通过

验收证据：CLI 的 `ticket-state`、`spec-state`、`thread-state`、`finish-action` 支持 Gate 输入；程序调用与 CLI 共用同一校验器；拒绝统一输出结构化 `decision=reject`，并验证拒绝不会改变持久化状态。
