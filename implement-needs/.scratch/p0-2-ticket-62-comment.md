已完成 P0-2 / #62：将统一 Gate 接入终态状态变更。

实现提交：`dde45bef7747e2209f50b15a5b174da030ca1211`

验证：
- `python -m unittest discover -s tests -v`：217 passed / OK
- P0-2 专项：`python -m unittest tests.test_p0_2_evidence_gate -v`：6 passed / OK
- `git diff --check`：通过

验收证据：ticket closed 必须匹配 commit/test evidence；thread archived 必须有 archive operation/readback；action succeeded 必须有 action readback；SPEC closed 必须确认 tickets 全部 closed、存在带归档证据的 archived thread，并再次通过终态 Gate。缺证据或身份/版本不匹配时 fail closed。
