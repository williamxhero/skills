已完成 P0-2 / #61：统一可信证据 Gate 与动作契约。

实现提交：`dde45bef7747e2209f50b15a5b174da030ca1211`

验证：
- `python -m unittest discover -s tests -v`：217 passed / OK
- P0-2 专项：`python -m unittest tests.test_p0_2_evidence_gate -v`：6 passed / OK
- `git diff --check`：通过

验收证据：新增无副作用的 `evidence_gate.py`，统一校验身份、授权、可信来源、readback、候选版本与业务版本；ticket/action/thread/spec 的终态入口均经同一 Gate，Gate 拒绝时不写状态、业务事件、业务版本或 evidence_refs。
