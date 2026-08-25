# StrategyReporting 合同

## 输入与职责

`strategy-reporting` 只消费 Quant Runtime 已发布的 reporting input 和 Apex Research 已发布的 protocol/trial/evidence/decision。它是 read-model 和展示层：不连接行情、不执行策略、不下单，也不计算或补造 engine metrics。

使用仓库当前 CLI 和 schema；先检查 `--help` 和同类 report。典型流程为对 run 使用 `render-run`，对研究记录使用 `render-study`，随后 `verify`、`rebuild` 并生成/更新 portal。不要从旧 `report.html`、日志或私有 artifact 路径二次统计，也不要直接手写最终 HTML。

## 人类报告内容

中文报告以决策阅读为先，呈现：策略身份与状态、形式化范围、数据完整性/更新时间语义、净值和回撤、原生收益风险和交易/成本指标、证据支持的归因、研究 decision、legacy 差异与限制。

图表与表格必须来自 Reporting model 支持的证据；缺失的维度直接写 `not_evaluated`。不展示内部命令、绝对路径、HTTP 故障、修复过程、重复 raw payload 或对用户无帮助的全量日志。

数据阻塞或只有研究设计时可生成说明性报告，但必须清楚标识：没有正式 run、没有新绩效，也没有可交易结论。

## 发布前 QA

1. 对 run/study render 后执行 `verify`；检查报告引用的发布身份。
2. 用同一输入 `rebuild`，确认模型/HTML artifact 可重建且稳定。
3. 生成 portal，确认不重复呈现同一 run/study/decision 的旧草稿。
4. 在 desktop 和窄 mobile viewport 检查可读性、色彩对比、关键指标层级和横向溢出。
5. 离线打开 portal/report；确认无外部 HTTP 依赖，并检查 browser console 无 error/warning。

正式报告可以链接公共 Workspace artifacts 和研究记录；不能要求读者访问私有 runtime 或数据库文件。
