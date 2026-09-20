已完成 P0-3 / #69：安全范围同步与最终回读。

实现提交：`b798648`（基于 `9faaba0`、`3cde21a`）

验证：
- `python -m unittest discover -s tests -v`：233 passed / OK
- P0-3 同步专项：3 passed / OK
- `git diff --check`：通过

验收证据：新增 run-scoped/full-project 两种同步计划；普通模式只选择授权路径并列出 preserved unrelated paths，显式请求越界路径立即拒绝。同步回读必须证明 verified、repository/ref/digest/candidate 一致，且 local_head=remote_head=candidate；full-project 未获显式授权不能记录同步成功。
