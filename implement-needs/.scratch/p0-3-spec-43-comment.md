## P0-3 验收结果

实现提交：`9faaba0`、`3cde21a`、`b798648`

已完成并关闭：

- #67：运行授权快照与范围门禁
- #68：候选冻结与证据一致性
- #69：安全范围同步与最终回读

交付结果：

- run authorization snapshot 不可变，绑定 repository/ref、允许路径/任务/动作、部署环境与 full-project 权限。
- 普通 run-scoped 同步只选择授权路径，无关脏改动明确 preserved；full-project 必须显式授权。
- candidate freeze 绑定授权 digest、candidate SHA 和 merge SHA；候选漂移显式 invalidated，旧证据不可复用。
- test/package/deployment/synchronization/final-readback 只能绑定 active candidate。
- 最终同步回读必须满足 repository/ref/digest 一致且 `local_head = remote_head = candidate`；仅 equality 不足以完成交付。

验证：

- `python -m unittest discover -s tests -v`：233 passed / OK
- P0-3 授权专项：3 passed / OK
- P0-3 候选专项：3 passed / OK
- P0-3 同步专项：3 passed / OK
- `git diff --check`：通过

P0-3 达到验收条件，可以按总控顺序进入下一子 SPEC：P1-3。
