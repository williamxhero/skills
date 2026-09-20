P1-1 已完成，三个子 ticket 均已关闭：#75、#73、#74。

交付提交：
- `b063c2d`：独立依赖图结构验证，区分未知/跨 run/循环/固定顺序错误与合法未交付状态。
- `082e848`：接入 `ready`/`waiting`/`blocked` readiness 和 scheduler；waiting 不创建 repair、不推进业务版本。
- `f70b4a2`：公开 dependency-check/readiness CLI 与 reconcile 投影，重启后可重算。

验证：全量测试 255 项通过。合法前置未完成保持 waiting；取消/失败前置无 waiver 为 blocked；结构错误才进入 repair。下一总控顺序为 P1-2（#46）。