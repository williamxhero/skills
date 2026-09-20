P1-4 已完成，子 ticket #79、#80、#81 均已关闭。

- `e212a41`：持久化 child bootstrap/route/assignment/cancellation 生命周期。
- `55e417d`：持久化每 SPEC L0-L3 义务、固定十 SPEC checkpoint，并在 due checkpoint 时阻塞下一段。
- `5b377a4`：公开 CLI、唯一 bootstrap/checkpoint 调度与重启/reconcile 回归。

验证：全量测试 267 项通过。该 SPEC 已合入 master；下一总控顺序为 P1-5（#53）。