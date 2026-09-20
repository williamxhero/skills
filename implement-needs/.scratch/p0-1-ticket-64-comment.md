已完成 P0-1 / #64：持久化运行级状态机与阶段回执。

实现提交：`aa86fab`

验证：
- `python -m unittest discover -s tests -v`：221 passed / OK
- P0-1 专项：`python -m unittest discover -s tests -p 'test_p0_1_runtime_state.py' -v`：4 passed / OK
- `git diff --check`：通过

验收证据：新增运行阶段 `initialized → preflight_passed → grilling → planning → implementing → final_verification → release → synchronization → completed`；新增 phase receipt 持久化、blocked/user_stopped/no_change 结果与恢复 API。非法跳转、身份不匹配、陈旧回执和重复终态均 fail closed；阶段、结果、回执及业务事件在同一事务中提交，重启后可恢复。
