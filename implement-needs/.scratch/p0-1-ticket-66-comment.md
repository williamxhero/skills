已完成 P0-1 / #66：公共 CLI 与恢复路径终态回归。

实现提交：`303322e`（基于 `aa86fab`、`7e4135a`）

验证：
- `python -m unittest discover -s tests -v`：224 passed / OK
- P0-1 CLI 专项：`python -m unittest discover -s tests -p 'test_p0_1_cli_recovery.py' -v`：2 passed / OK
- `git diff --check`：通过

验收证据：公共 CLI 可初始化运行、推进阶段、拒绝陈旧回执、查询阶段动作并记录 no_change；拒绝时返回结构化 `decision=reject` 且不改变事件/状态；重新通过 CLI 打开数据库后仍保留 planning 阶段与 no_change 终态，空运行不会被报告为 completed 或 release。
