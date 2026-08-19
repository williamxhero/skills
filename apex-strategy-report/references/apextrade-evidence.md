# ApexTrade 证据规则

仓库根目录：`D:\WILL\STOCK\apex_proj\apex-trade`。回测可执行程序通常为 `target\debug\backtest-tool.exe`；以当前仓库的 task 配置和 CLI 帮助为准，不要根据旧命令猜参数。

正式 run 的最小证据：

- 实际命令与退出状态。
- task 配置副本、策略 ID、策略包路径/哈希和输入时间范围。
- `run_manifest.json`：本次运行的身份、策略、数据和执行元数据。
- `portfolio_summary.json`：组合主绩效摘要（存在时）。
- `account_curve.csv`、`drawdown_curve.csv`、`profit_curve.csv`：曲线来源。
- `artifacts.json`：产物目录和完整性索引（存在时）。
- `orders.jsonl`、`trades.jsonl`、`position_snapshots.jsonl`：交易与持仓审计（存在时）。
- `report.html`：ApexTrade 的原生视图，只作链接或人工核对，不作二次统计源。

读取规则：

1. 优先从 `portfolio_summary.json`、曲线 CSV 和 JSONL 审计流生成表格和图表。
2. `run_manifest.json` 记录运行身份，不替代主绩效统计。
3. `report.html` 不能用来反推或补造结构化指标。
4. 有 `artifacts.json`、`portfolio_summary.json` 或 `account_curve.csv` 的 structured output，重新渲染原生报告时必须保留 `account_curve.csv`。
5. 目录只含 `contract_results.json` 和单合约子目录时，不是组合级输出；报告必须指向真实组合级目录或明确改为单合约研究。

默认验证顺序：短区间冒烟、冻结区间正式 run、基线比较、时间分段/样本外、参数敏感性、成本压力。没有完成的步骤必须被报告为未完成，不得静默省略。

## 证据展示边界

ApexTrade 命令、可执行文件路径、schema 兼容、原生报告重建过程和工具字段缺失都属于内部运行证据，写入 `runs/<运行标签>/run_record.md`。正式策略报告只展示由这些证据支持的策略指标、成交假设和研究结论，不展示工具排障过程。
