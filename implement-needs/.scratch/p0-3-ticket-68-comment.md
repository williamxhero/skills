已完成 P0-3 / #68：候选冻结与证据一致性。

实现提交：`3cde21a`（基于 `9faaba0`）

验证：
- `python -m unittest discover -s tests -v`：230 passed / OK
- P0-3 候选专项：3 passed / OK
- `git diff --check`：通过

验收证据：新增 candidate freeze/evidence 持久化；冻结记录绑定 candidate SHA、merge SHA 与 authorization digest；test/package/deployment/synchronization/final-readback 证据必须匹配 active candidate。候选不匹配、授权 digest 不匹配或旧 active freeze 尚未失效时拒绝写入；显式 invalidation 保留旧证据并要求新候选重新冻结。
