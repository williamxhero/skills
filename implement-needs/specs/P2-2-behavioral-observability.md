# SPEC P2-2: 以行为安全指标和覆盖率评价运行效率

## Problem Statement

当前指标容易被误当成行为保障：重复外部操作数可能只是 metadata 布尔值，Fake runtime 主要写入 observation 而没有执行完整交付闭环。少记录、少检查可能让 Token 或轮数变低，却掩盖错误完成、重复副作用和恢复失败。缺失的 Token、费用或来源数据也没有明确标记覆盖率。

## Solution

将观测覆盖率与实际安全断言分开。建立固定评测任务集，同时记录成功交付率、错误完成率、重复外部操作、恢复成功率、控制器轮数、输入/输出 Token、费用和数据缺失比例。Fake 场景与真实动作闭环测试使用不同命名和结论；性能比较必须保持完成条件和安全检查不变，未知指标明确标为未知而非零。

## User Stories

1. As a maintainer, I want efficiency measured alongside delivery correctness, so that lower Token use cannot hide unsafe behavior.
2. As a reviewer, I want error-completion rate reported, so that false success is visible as a primary regression.
3. As an operator, I want duplicate external operations and recovery success measured from real lifecycle evidence, so that metadata claims do not substitute for behavior.
4. As a benchmark owner, I want a fixed task set, so that before/after comparisons are reproducible.
5. As a cost owner, I want input/output Token and known fees reported with coverage, so that missing data is not silently treated as zero.
6. As a test author, I want Fake runtime tests clearly separated from end-to-end external-action tests, so that their claims are not overextended.
7. As a controller, I want instrumentation to be read-only with respect to completion gates, so that metrics cannot alter delivery semantics.
8. As a user, I want final reports to state measurement limits, so that performance claims are appropriately qualified.

## Implementation Decisions

- Define separate observability fields for behavior evidence, test coverage, and measurement availability.
- Derive duplicate-operation counts from Intent/Action lifecycle and provider/readback evidence where available; do not accept a caller Boolean as proof.
- Track delivery success, false completion, recovery outcomes, controller turns, latency, tokens, fees, and missing-data coverage.
- Mark absent Token/fee/provider data as unknown with coverage metadata; never coerce unknown to zero.
- Name Fake runtime scenarios as simulation/coverage tests and reserve safety claims for public end-to-end lifecycle tests.
- Keep all safety gates and required checks identical between benchmark variants; optimize only within that fixed protocol.

## Testing Decisions

- Run the fixed benchmark task set through simulation and real/contract-backed lifecycle tests and compare claims separately.
- Inject duplicate, lost-response, failed-recovery, and false-success scenarios and assert metrics reflect observed behavior.
- Verify metric collection cannot advance phases, complete Actions, or close Tickets.
- Test missing Token, fee, and provider data and assert unknown plus coverage output.
- Verify performance reports include correctness gates and do not declare “no duplicates” from absent observations.
- Use runtime metrics and fake backend tests as prior art, adding end-to-end acceptance cases.

## Out of Scope

- Optimizing model prompts or choosing a new model.
- Changing required acceptance or security checks to improve benchmark numbers.
- Providing provider metrics that the provider cannot expose.

## Further Notes

This SPEC is the last implementation step in the recommended sequence. First make completion and side-effect behavior correct and observable; only then optimize context, tool output, turns, or cost. A missing metric is a reporting limitation, not evidence of safety.
