P1-3 已完成，三个子 ticket 均已关闭：#70、#71、#72。

交付提交：
- `fb48813`：可重放 startup contract、依赖 canonical/alias 解析、路径/摘要/版本/adapter 校验、契约摘要不可变持久化。
- `4fcc6be`：公开 decision CLI/API，强制 actor/scope/rationale/source/授权关系，分离 decisions、observations、business evidence。
- `869afcc`：发布启动参考索引、最小命令示例、CLI/重启回归。

安全结果：缺失/歧义/lookalike/不匹配依赖 fail closed；启动验证与 decision 校验失败不产生业务版本、事件或业务证据副作用；重启后各审计流保持可读且分离。

验证：全量测试 246 项通过。

依赖顺序中的下一项是 P1-1（#45）；P1-3 完成后才允许进入该 SPEC。