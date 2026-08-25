# 研究能力与展示

## 原则

报告只展示 Quant Runtime 已发布、Apex Research 已引用且 StrategyReporting 模型支持的证据。字段存在、旧系统曾显示或能主观推导，都不构成可展示证据。不能由发布模型支持的能力标记为 `not_evaluated`，并说明缺少的原生输入或研究设计。

## 证据层

| 层级 | 可展示的典型内容 | 边界 |
| --- | --- | --- |
| Formal run | package/revision、范围、数据 identity、权益、回撤、Nautilus 统计、orders/fills/positions、成本 | 只引用对应 frozen run 与 native reporting input |
| Apex Research | source mapping、protocol、trial、gates、evidence、decision | decision scope 不扩大为投资建议 |
| StrategyReporting | 人类可读汇总、图表、表格、限制、portal | 不能产生新 execution/metric/attribution |

## 默认展示

有完整正式 run 时，优先展示策略身份、正式区间、数据完整性、资金曲线、回撤、初末权益、累计收益、原生 Sharpe/Sortino/波动、交易数、胜率、期望、费用/滑点语义、long/short 或仓位语义（若模型提供）和 Apex Research decision。

按证据可用性展示年度收益、交易活跃度、成本归属、最大回撤峰谷与恢复期、品种参与度等。每个图表邻近说明它回答的问题和限制；不要为“页面完整”复制原始数据表。

## 条件能力

以下仅在对应原生或研究证据存在时展示：

- 基准、alpha/beta、捕获率；
- 产品、行业、风格、因子、退出原因或市场状态归因；
- 样本外、walk-forward、参数敏感性、压力测试、bootstrap；
- 市场冲击、容量、成交概率、PIT 暴露与可交易性。

任一缺少时使用 `not_evaluated`，不能从交易明细、legacy report 或视觉图形手工反算。报告可提出后续研究，但必须区分计划与完成证据。

## 面向人的优先级

首页应先回答：运行的是什么、在哪个区间和数据口径上、结果如何、最大风险何时发生、成本怎样处理、研究接受了什么以及尚未证明什么。详细表格放后层或附录，避免绝对路径、hash、接口日志和运维过程。

对于 legacy 迁移，使用并列的“legacy evidence / formal result / difference”结构：保留旧指标出处，明确它不是新 run 结果；列出差异而不宣称精确复制。
