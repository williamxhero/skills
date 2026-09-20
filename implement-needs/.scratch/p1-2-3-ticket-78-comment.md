P1-2.3 已实现并验证。

- 提交：`29ea50c test(implement-needs): verify action recovery restart boundary`
- 公开 CLI 与重启回归覆盖 prepare-intent、intent-outcome、next-action 优先 reconcile，以及 SQLite snapshot 的 intents/claims/recovery 审计投影。
- 丢失响应不会被下一进程误判为成功或直接重发。
- 验证：专门测试 1 项通过；全量测试 261 项通过。