## Parent

[#101](https://github.com/williamxhero/skills/issues/101)

## What to build

把预算 admission、overflow 和 fallback 接入公开 context/measurement CLI 及固定 P3 benchmark，验证 P3-1、P3-2 和 P3-3 的候选版本、预算配置、bytes/tokens 与安全结果一致，并完成 P3 总控的最终回归和主干同步证据。

## Acceptance criteria

- [ ] 公共 CLI 能区分 normal、near-limit、overflow、missing-pointer 和 unreachable-pointer 结果。
- [ ] overflow 永不返回成功完成结论，也不静默删除 required field。
- [ ] 五类固定 fixture 都有 budget/fallback 报告，Provider Token/fee 缺失保持 unknown。
- [ ] 只有 safety checks 全部通过且候选有真实改进时才返回 allow，否则为 inconclusive/blocked。
- [ ] #57 全量安全回归通过，P3-1/P3-2/P3-3 tickets 与 SPEC 关闭，master 与 origin/master 一致。

## Blocked by

- [#107](https://github.com/williamxhero/skills/issues/107)
