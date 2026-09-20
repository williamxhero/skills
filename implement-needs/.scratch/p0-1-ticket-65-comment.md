已完成 P0-1 / #65：阶段驱动的安全调度与终态判定。

实现提交：`aa86fab`、`7e4135a`

验证：
- `python -m unittest discover -s tests -v`：222 passed / OK
- P0-1 专项：`python -m unittest discover -s tests -p 'test_p0_1_runtime_state.py' -v`：5 passed / OK
- `git diff --check`：通过

验收证据：`next_action` 现在先处理结构性队列修复，再依据持久化 run phase 返回唯一阶段动作；planning 阶段空 SPEC 只返回 `confirm_no_change`，有 SPEC 才能进入 implementing；所有 SPEC 未进入 closed/cancelled 前不能进入 final_verification；final_verification、release、synchronization、completed 严格按顺序推进。终态结果从数据库读取，不再由空集合推断成功。
