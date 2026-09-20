已完成 P0-3 / #67：运行授权快照与范围门禁。

实现提交：`9faaba0`

验证：
- `python -m unittest discover -s tests -v`：227 passed / OK
- P0-3 授权专项：3 passed / OK
- `git diff --check`：通过

验收证据：新增 schema 版本 7 的不可变 run authorization snapshot 与 SHA-256 digest；支持 repository/ref、allowed paths/tasks/actions、deployment target、full-project 权限；`auth-check` 为无副作用纯校验，越界动作、路径、ref、环境和 full-project 均结构化拒绝，且拒绝不改变业务版本或事件。旧 run 显式保留为 `unconfigured`，范围敏感操作必须先配置授权。
