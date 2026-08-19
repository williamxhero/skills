---
name: apex-strategy-report
description: Use ApexTrade at D:\WILL\STOCK\apex_proj\apex-trade to turn a supplied strategy article URL, local document, or conversation record into a reproducible Chinese strategy research website with executable rules, portfolio simulation, full performance metrics, attribution, robustness, capacity and structured evidence. Trigger for requests such as "查看文章并写策略报告", "根据 DOC/聊天记录写策略报告", "策略研究报告", "完整策略研究", "策略归因/稳健性分析", or "用 apex-trade 回测后出报告".
---

# Apex 策略报告

将输入资料转化为可验证的策略研究，而不是只改写文章。固定使用 `D:\WILL\STOCK\apex_proj\apex-trade` 做策略实现、回测和结构化指标生成。股票策略通过 `D:\WILL\STOCK\apex_proj\market-hub-adapter` 访问小电脑 `yosef-server` 上的 `MarketHub`，先生成 ApexTrade 已有 SQLite 输入契约，再运行 ApexTrade。

## 输入与输出

只接受一个明确的主输入：文章 URL、单个本地文档，或当前对话中明确指定的一段聊天记录。将原始输入冻结到策略工作区的 `source/`；URL 保存抓取日期、URL 和提取文本，文档保留原件副本及提取文本，聊天记录保存逐字摘录。

优先使用用户明确给出的目标目录。用户未给目录时，默认在以下根目录创建策略工作区：

`D:\WILL\STOCK\apex_proj\strategies`

默认工作区为 `D:\WILL\STOCK\apex_proj\strategies\<策略短名>\`；`<策略短名>` 使用小写 ASCII、数字和连字符，并表达策略规则而不是文章来源。若同名工作区已经存在，复用它；不得新建带日期的并列策略目录。

用户指定的是空目录时，直接将该目录作为策略工作区；指定目录已有无关文件时，在其中新建 `<策略短名>/`。不得覆盖已有研究。默认工作区必须组织为：

```text
<策略工作区>/
  source/
  strategy/
  research/
  runs/
  reports/
    YYYY-MM-DD-<研究版本>/
      index.html
      methodology.html
      reproduction.html
      robustness.html
      diagnostics.html
      execution.html
      appendix.html
      assets/
```

将策略实现放在 `strategy/`，将每次 ApexTrade 正式运行放在 `runs/<运行标签>/`，将最终静态报告放在 `reports/YYYY-MM-DD-<研究版本>/`。同一策略可有多个运行和报告；报告必须引用对应 run 的相对路径。

每个报告目录必须包含：

```text
<报告目录>/
  index.html
  methodology.html
  reproduction.html
  robustness.html
  diagnostics.html
  execution.html
  appendix.html
  assets/
```

制作报告前必须阅读 `references/report-book-contract.md` 和 `references/full-research-capabilities.md`。运行或解释回测前必须阅读 `references/apextrade-evidence.md`。

将面向读者的策略报告与内部运行记录分开：

- `reports/<版本>/` 只展示策略规则、数据口径、成交假设、指标、归因、稳健性、容量、结论与研究局限。
- `runs/<运行标签>/run_record.md` 记录实际命令、绝对路径、哈希、Adapter/工具状态、数据修复、失败、重试、合同检查和实现边界。
- 不得把数据管道修复、工具排障、编译过程、内部文件全量清单或本机目录写进正式策略报告。

## 固定流程

按以下阶段执行；不得跳过证据边界。

### 1. 资料冻结

提取并区分：

- 作者明确陈述的交易规则、标的、频率、调仓时点、参数、手续费与样本区间。
- 作者给出的绩效数字和图表。
- 未说明但回测必须决定的假设。

将前两项标为 `source evidence`，将第三项标为 `research assumption`。缺失信息不得伪装为原作者规则。

### 2. 策略规格

在策略工作区的 `research/strategy_spec.md` 写出唯一可执行的策略定义：数据宇宙、信号公式、过滤条件、持仓目标、调仓/成交时点、仓位、退出规则、成本、滑点、停牌和涨跌停处理、初始资金与回测区间。

在规格中列出 `不可复现项`。若输入不足以定义可执行策略，停止在研究设计阶段：仍生成报告，但把结果明确标为“未回测”，并给出待确认项和运行计划。不得捏造交易、净值、收益、回撤或稳健性结论。

### 3. ApexTrade 执行

#### 3.1 小电脑 MarketHub 唯一原始数据源合同

对 A 股/股票策略，正式研究使用的原始行情、因子、财务、公司行动和证券基础数据，唯一允许的上游是小电脑 `yosef-server` 上的 MarketHub，固定地址为 `http://yosef-server:8803`。

固定数据链路为：

`小电脑 MarketHub → market-hub-adapter → stock_trade_stat_snapshot.sqlite → ApexTrade`

- 禁止从 `127.0.0.1`、`localhost`、`::1`、Windows 本机 MarketHub、本地 WSL MarketHub 或本地 MySQL 读取正式研究的原始数据。
- 禁止把本机环回地址作为探测、镜像、替代源或失败后的自动回退。测试夹具可以使用无效环回地址验证失败行为，但不得进入正式研究运行。
- 本地 CSV、SQLite 和缓存只允许作为已经由小电脑 MarketHub 明确导出并冻结的下游研究快照；必须保留 dataset ID、source URL、时间窗口、内容 hash、manifest、quality 和 lineage，不能把来源不明的本地文件当成原始数据。
- 每次正式运行前，必须检查实际生效的 Adapter 配置和环境覆盖值，确认 `base_url` 精确指向 `http://yosef-server:8803`。只要解析后是本机地址、环回地址或其他主机，立即停止。
- 小电脑 MarketHub health、网络、字段或历史覆盖不满足要求时，停止并向用户说明阻塞原因；报告状态降为“研究设计”或“数据阻塞”。不得改用本地源继续回测，也不得把不完整结果包装成正式结论。
- 策略文章、规则文档和人工参数不是市场原始数据，可继续从用户提供的 URL、本地文档或会话记录读取；本合同只约束策略研究的数据上游。

#### 3.2 MarketHub 固定预检

在任何 ApexTrade 数据预检或回测命令前，先确认配置中的 `base_url = "http://yosef-server:8803"`，再执行：

```powershell
Set-Location D:\WILL\STOCK\apex_proj\market-hub-adapter
& .\scripts\run_preflight.ps1 -ConfigPath .\config\adapter.example.toml
& .\scripts\check_contract.ps1 -SnapshotPath .\runtime\markethub_adapter\final\stock_trade_stat_snapshot.sqlite
```

#### 3.2.1 正式历史窗口与因子门禁

当研究区间超出现有 final 快照时，必须先在 `market-hub-adapter` 生成覆盖该区间的正式 SQLite，再运行 ApexTrade。

- 使用 `scripts/run_formal_quarter.ps1` 逐窗口生成，或使用 `scripts/run_formal_monthly_snapshots.ps1` 串行生成连续月度窗口。每个窗口都必须依次完成 preflight、Adapter run 和 `check_contract.ps1`。
- 原始日线与每个已请求的复权基准必须在 `(code, trade_date)` 上严格一一对应。复权缺口、重复键、截断响应或未完成的 `meta` 都必须阻止 final 发布；不得在 Adapter 中以静默补值放宽这一门禁。
- 对每只保留证券，原始日线必须覆盖其上市至退市窗口内的全部目标交易日；“原始与复权同时缺同一天”不能视为对齐。`stock_strategy_factor_window` 中的每个 `(code, trade_date)` 必须存在对应的原始日线键，最终 SQLite 合同检查必须拒绝违反这一关系的快照。
- MarketHub 的前复权价格可由本地日线与有效复权因子派生；因子缺失只能在同一证券已有有效因子之后按时间向前延用。整只证券没有有效因子时，必须保留为缺失，不能伪造复权价格。
- 策略实际使用的字段必须检查 `stock_strategy_factor_window` 和 `stock_strategy_factor_contract`。仅消费 `readiness=ready` 的 MarketHub 字段；`blocked` 或 `not_equivalent` 字段必须让该策略运行停在研究设计状态，不能换成近似字段。
- 正式研究可按 MarketHub 口径逐证券排除缺少必要字段的标的，但不得以空值、零值或不相关字段填充。报告必须记录每个调仓点的候选覆盖和排除原因。
- 只接受 `final/stock_trade_stat_snapshot.sqlite` 与同目录的 manifest、quality、lineage、adapter_run 作为 ApexTrade 输入证据。`staging/` 仅为中间产物，永远不可作为回测输入。
- 批量快照任务在第一个 preflight、数据、对齐或合同错误处必须停止，不能继续创建后续月份的无效目录。失败窗口应保留失败证据或做可恢复归档，成功窗口才可加入合并。

同时读取并记录：

- `runtime/markethub_adapter/final/dataset_manifest.json`
- `runtime/markethub_adapter/final/data_quality_report.json`
- `runtime/markethub_adapter/final/adapter_run.json`

如果正式研究窗口不在现有快照中，必须复制 `config/adapter.example.toml` 到策略工作区的 `runs/<运行标签>/adapter-task.toml`，设置明确的 `window.start_date`、`window.end_date`、A 股 universe、qfq/hfq、公司行动和资源上限，然后运行：

```powershell
& .\target\debug\market-hub-adapter.exe run .\runs\<运行标签>\adapter-task.toml
```

Adapter 的 `staging/` 是中间产物，`final/` 是可供 ApexTrade 消费的最终产物；报告必须记录 dataset ID、source version、内容 hash、row counts、warnings 和 `not_exportable`。不得把 staging 数据直接当作 ApexTrade 输入。

#### 3.3 MarketHub 能力与策略字段门禁

MarketHub 可能已经提供股息率、日度估值、市值、股本、资金流、技术因子、财务三表、分红和公司行动，但“API 有字段”不等于“Adapter 已导出”或“字段语义与文章一致”。运行前必须建立字段映射表，并将每个字段标为 `exact`、`derived` 或 `not_equivalent`：

- `exact`：字段名、单位、时间可见性和历史覆盖均确认；
- `derived`：由明确输入派生，公式、PIT 和单位写入 `research/strategy_spec.md`；
- `not_equivalent`：不得静默替代，必须停在“研究设计”或由用户明确批准代理策略。

尤其要单独核对股息率、流通市值、主动买入占比、EBIT 每股、基本 EPS 连续增长、扣非净利润 TTM 连续增长和涨跌停字段。

MarketHub health 通过但 Adapter 输出契约不完整时，报告状态应写为“研究设计”，原因应写成“因子导出/语义契约未闭环”，不得写成“MarketHub 不可用”。

#### 3.4 ApexTrade 策略执行

先检查 `apex-trade` 的当前任务配置、可用策略包和由 Adapter 生成的数据可用性；只复用与策略规格相符的现有任务或策略。需要新策略时，在当前策略工作区的 `strategy/` 实现、编译为 Wasm、打包为 `.apx`，再由 ApexTrade 加载；不要把研究代码混进 `apex-trade` 仓库。

先运行一个短区间冒烟回测，再运行冻结区间的正式回测。每个结论性 run 都必须保留实际命令、task 配置副本、策略包标识和时间范围。将正式的 ApexTrade 输出目录完整复制或链接到 `runs/<运行标签>/apex-output/`，保持其目录内相对路径不变。

仅从结构化产物读取指标并生成网页；不得从 `report.html` 反推数值。回测失败、数据预检失败或结果不完整时，报告必须显示失败原因及缺失证据，而不是用估计结果填补。

### 4. 验证与报告

将验证设计写入策略工作区的 `research/validation_plan.md`：至少包含基线、样本内外切分或时间分段、参数敏感性和交易成本压力测试。仅在数据和策略定义支持时执行这些测试；未执行的检验必须写为未完成。

#### 4.1 长周期与新鲜度门禁

完整策略研究默认使用 5 至 10 年历史，并尽量截止数据源中最新的完整交易日。开跑前必须核对正式价格源的最早日期、最新日期、证券数和关键字段覆盖，不得仅根据文件名推断。

- 正式样本应覆盖至少一个明显强势期和一个明显弱势期；只有短窗口时，报告状态不得写为“已验证”。
- 优先让严格样本外区间本身达到 5 年；若数据长度不足，则采用至少 5 年总历史、预先冻结的训练/样本外切分和滚动验证，并明确样本外实际长度。
- 记录数据截至日与报告日的自然日差；数据源可用时先刷新到最新完整交易日，再冻结为新版本。不得覆盖旧快照或静默更新旧报告。
- 长周期需要的复权字段覆盖不足时不得静默回退。替代价格连续方案必须显式命名、写明断点规则、创建新研究版本，并与原复权口径区分。
- 训练期、样本外和最终未触碰测试集按可用历史预先冻结。任何在样本外观察后新增的过滤条件都属于新假设，必须另开版本重新验证。

默认按 `references/full-research-capabilities.md` 执行所有适用研究层：共享资金组合、收益风险、交易统计、基准、归因、压力测试、滚动验证、Bootstrap、容量和集中度。某项能力缺少必要数据时，明确写成未完成及原因，不得用当前截面、近似字段或主观判断补造。

生成静态 HTML 报告站点。页面标题、正文和表格使用中文；文件名保持上述英文约定。默认入口为 `index.html`。使用现有 `book` 的页面结构和视觉语言作为参考，不复制旧报告的策略结论或数值。

对已完成且结果完整的回测，报告必须在 `index.html` 的核心结论区包含净值曲线图，以及由对应运行结构化产物计算或读取的关键收益风险指标：年化收益率、最大回撤、卡玛比率、夏普率。指标表还应展示可从同一结构化产物可靠取得的其他关键绩效数据。每项指标与净值曲线都必须标明对应运行和回测区间；不得以截图、手工估算或 `report.html` 反推数值替代结构化证据。未回测、回测失败或产物不完整时，不得伪造图表或指标，应明确显示“未回测”或失败原因及缺失证据。

对已完成的完整研究，七页合计默认至少展示 6 张决策型图表，至少覆盖净值/基准、回撤、年度或月度收益、归因、压力/参数面、滚动或容量。图表必须来自结构化产物并紧邻解释；数字表格作为可展开证据层，不得让页面以大段原始数字为主要视觉。

首页至少给出 5 条有交易指导意义的结论。每条按“证据 → 含义 → 行动 → 失效条件”组织，行动必须是当前证据支持的仓位、观察、暂停、复核或执行约束，不得把归因分组直接包装成已经验证的新增过滤规则。至少一条结论必须陈述不利证据或不可做事项。

正式报告只解释会改变策略含义、收益、风险或可信度的数据事项。数据修复过程、Adapter 月度任务、合同检查次数、不可导出提示、工具命令、编译日志、绝对文件路径、原生/外部实现争议等内部信息必须保留在 `run_record.md`，不得进入网页正文。若实现差异会实质改变成交结果，只在报告中写对应的成交假设和影响，不写工具内部原因。

生成后运行 `scripts/validate_strategy_report.py <报告目录>`，检查七页结构、链接、首页指标和内容纯度。

## 完成标准

交付前确认：

- `index.html` 能在浏览器直接打开，并链接到每个章节页面。
- 已完成且结果完整的回测报告在 `index.html` 展示净值曲线图，并展示年化收益率、最大回撤、卡玛比率和夏普率等关键收益风险指标；图表和指标均可追溯到对应结构化运行产物。
- 完整研究默认覆盖 5 至 10 年并尽量截至最新完整交易日；不足时已降低状态并明确缺口、数据时滞和替代验证设计。
- 七页至少有 6 张决策型图表，且核心结论按“证据、含义、行动、失效条件”给出，不以数字表格代替解释。
- 每个出现的绩效数字都能追溯到对应 `runs/<运行标签>/apex-output/` 的结构化文件，或清楚标注为来源原文。
- 页面明确给出策略版本、策略相关的数据口径与来源角色、回测区间、成本与成交假设、局限性和研究结论；实际命令保留在运行记录。
- 页面只包含与策略规则、数据口径、绩效、归因、稳健性、容量和风险直接相关的信息；运行过程与数据修复已移入 `runs/<运行标签>/run_record.md`。
- 所有适用研究能力已按 `references/full-research-capabilities.md` 执行并展示结论；未适用或缺数据的能力已明确标注。
- `source/`、`strategy/`、`research/`、`runs/` 与 `reports/` 分别承载原始输入、策略实现、研究规格、回测产物和最终网页，不混放。
- 对使用 MarketHub 的股票研究，`runs/<运行标签>/adapter-task.toml`、Adapter manifest/quality/lineage 和最终 SQLite 快照必须与 ApexTrade task、策略包和 `apex-output/` 一起归档；报告要能从 ApexTrade 输出追溯到 Adapter dataset ID。
- 正式运行的 Adapter 配置与 lineage 已证明唯一上游为 `http://yosef-server:8803`，且运行记录中不存在从 `127.0.0.1`、`localhost`、`::1` 或本地 MySQL 读取正式数据的路径。
- 若未实际完成回测，不得把报告描述为已验证、可实盘或有预期收益。
