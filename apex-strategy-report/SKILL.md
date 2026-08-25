---
name: apex-strategy-report
description: "Turn a strategy article, document, chat record, or legacy evidence into a reproducible Chinese QuantResearch study: canonical Strategy Package, MarketHub-backed Qlib/Nautilus research, Apex Research evidence, and deterministic StrategyReporting HTML. Use for strategy research, migration, backtests, attribution, robustness, or research reports; pause and open a visible MarketHub repair task when live data fails."
---

# QuantResearch 策略研究报告

将来源资料变成可验证的 QuantResearch 研究交付，而不是复述文章或手工制作网页。正式链路是：

`source/legacy evidence → strategy-workspace → quant-runtime → apex-research → strategy-reporting`

`D:\WILL\STOCK\QuantResearch` 是默认工程根。读取目标仓库的 `AGENTS.md`、CLI/README、现有 immutable package/run/study/report 后再行动；优先复用相同身份的已发布对象，避免重复运行。

## 何时使用与边界

适用于文章、文档、聊天记录或只读 legacy 策略证据的策略研究、策略迁移和中文研究报告。它不用于把旧 ApexTrade 重新作为正式回测器，也不用于臆造数据、指标或报告。

旧 `ApexTrade`、旧策略目录和旧报告只能作为只读来源证据：提取规则、参数、数据范围、成本、原始结果和不可复现差异。不得运行旧引擎，亦不得把旧收益当新结果。

先按任务选择所需参考：

- 跨仓实现或写入：阅读 [QuantResearch 架构](references/quantresearch-architecture.md)。
- 调查数据或遇到 live 数据失败：阅读 [MarketHub 修复任务](references/markethub-repair-task.md)。
- 冻结来源、建立规格、解释正式结果或 legacy 差异：阅读 [证据与研究边界](references/quantresearch-evidence.md)。
- 渲染或评审面向人的网页：阅读 [StrategyReporting 合同](references/strategy-reporting-contract.md)。
- 判断哪些研究能力可展示：阅读 [研究能力与展示](references/full-research-capabilities.md)。

## Canonical ownership

保持边界清晰，不以跨仓私有文件耦合：

| 组件 | 唯一职责 | 禁止事项 |
| --- | --- | --- |
| `strategy-workspace` | Strategy Package、参数合同、请求、状态、artifact 注册 | 直接读 SQLite/私有 artifact、实现运行时账本 |
| `quant-runtime` | MarketHub-only 数据接入；Qlib discovery；Nautilus 正式执行 | 把 discovery 伪装为正式结果、计算新指标替代 Nautilus |
| `apex-research` | protocol、trial、gate、evidence、decision | 充当回测引擎或改写 engine metrics |
| `strategy-reporting` | read-model 和面向人的展示 | 下单、执行、编造指标或手写结果 HTML |

跨组件仅用公共 `WorkspaceClient`/CLI 和已发布 artifact。不得直接读取 Workspace SQLite、私有目录、运行时数据库或 artifact 文件路径。

## 先检查现有研究，不要猜 CLI

每个阶段先在 owning repository 的当前版本中检查 README、`AGENTS.md`、CLI `--help` 和同类实现。命令名、request schema、artifact schema 和发布能力都可能随 revision 改变；既往任务记录只能提供线索，不能替代当前证据。

用公共查询确认下列 immutable identity，随后才决定复用、retry 或新建：

- package ID、revision、manifest/hash、参数和 topology；
- canonical request、run、attempt、状态及 frozen snapshot；
- dataset、catalog、calendar、query/coverage、成本与保证金 profile；
- Apex Research protocol/trial/evidence/decision；
- StrategyReporting report、model artifact 和 portal publication。

相同 identity 的完成对象应复用。失败 attempt 是审计证据，不得覆盖；只有 Workspace 明确允许且身份不变时才 retry。新建对象前说明到底是哪一项 identity 改变。

## 起步：先冻结而非猜测

1. 记录输入原文、URL/文档身份、提取时间和不可变副本；区分 `source evidence`、`legacy evidence`、`research assumption`。
2. 查找既有 Strategy Package、run、study、decision、report。身份、版本或数据范围相同则复用；不为“再看一次”新建平行研究。
3. 将规则落实为一个唯一可执行规格：宇宙、频率、价格/adjustment、信号、时点、仓位、加减仓和退出、初始资金、成本、滑点、保证金、日历和区间。
4. 缺失的决定性语义列为不可复现项。若无法构成可执行规格，交付 `research_design`，不得启动形式化回测或填补绩效。

对 legacy 迁移，逐项核对报告、配置、代码和产物；先找目标/最高收益候选的精确证据，再命名 Package。来源指标只写作对照证据，不硬编码为新结果。

## Strategy Package

在 `strategy-workspace` 创建或更新正式包，作为 canonical owner。提供 manifest、参数 schema、冻结默认值、实现、测试、可复现 request 样例和迁移说明。

- 默认研究拓扑为 `formal_only`；只有确有候选生成需求时使用 `discovery_formal`，并隔离 Qlib discovery 与正式 Nautilus 结果。
- Strategy Package ID、revision、参数、数据/时间语义、成本模型和执行身份必须可追溯。
- 优先直接用 Qlib/Nautilus 的已有能力；不要迁移 ApexTrade 的重复引擎、订单、成交或统计实现。
- 冻结策略不得因报告或运行便利而改参数。若语义、配置或身份变化，创建新 revision/request。

先运行包级单元测试和公共合同检查；确认不会改变既有 packages（尤其第一个 momentum 包）。

### 资料与版本化

来源文件、旧配置和报告截图可以被冻结为 evidence，但不要把它们混入 package runtime。迁移说明应列出规则映射、明确采用的假设、未能一比一复现的语义，以及 legacy 数字的出处。

若用户要求“可执行”，这意味着 schema 能拒绝无效值、manifest 能表达运行身份、实现可被 Runtime 加载、至少有结构与行为测试；它不自动意味着已经通过 live 数据门、正式回测或投资批准。

## MarketHub preflight 是正式门

正式行情唯一来源是小电脑 MarketHub 的 `http://yosef-server:8803`。禁止使用 `localhost`、环回/本机地址、其他 host 或本地 snapshot 作为正式路径或 fallback。fixture 只能做结构测试；旧数据和 legacy 指标绝不可替代正式结论。

在创建 run 前，用将要执行的请求做 live preflight，记录 endpoint、query、HTTP/payload、产品/字段/频率/adjustment、区间、coverage、versions、catalog/calendar、PIT 和 time semantics。先区分：

- 上市前无数据、休市、用户参数错误或策略明确排除；这些不是数据故障。
- 真正的数据、服务、合同或版本故障；遵循下节状态机。

不要因为数据不足缩短范围、移除品种、换数据源、改参数、用缓存，或反复启动相同回测。详细判定和证据字段见 [MarketHub 修复任务](references/markethub-repair-task.md)。

## 强制 MarketHub 修复状态机

任一 health、HTTP、payload、coverage、field、calendar、catalog、version、order、duplicate、truncation、PIT 或 time-semantics 故障时：

1. **暂停主研究。** 不注册/重跑正式 run；不 fallback、不缩短范围、不用 fixture/local snapshot/旧指标。
2. **捕获 fingerprint。** 按 [MarketHub 修复任务](references/markethub-repair-task.md) 的 canonical JSON/SHA-256 规则保存稳定指纹和诊断证据；没有 Workspace run 时不要为保存它创建伪 run/record。
3. **创建或复用可见修复 task。** 用户的 standing authorization 已明确满足此类独立用户可见 MarketHub task/thread 的 `create_thread` 授权条件。线程工具可用时，先 `list_threads` 按 fingerprint 查找 active task；命中时用 `send_message_to_thread` 追加证据。未命中才 `list_projects`，优先选择 QuantResearch saved project，再以 `create_thread` 创建修复 task（git repo 默认 worktree）。
4. **等待而不重复。** 主 task 用 `wait_threads` 与 cursor 监控修复 task；不循环 `read_thread`，不重复 preflight/run。隐藏 subagent 不能冒充用户可见 task。
5. **修复到 live 验证。** 修复 task 按根及目标仓 `AGENTS.md`、跨项目 owner 规则，修复、测试并部署到 `yosef-server`；持续到原始 live query 完整通过，并返回 commit、push、deploy、dataset/catalog/calendar 证据。
6. **独立复验和恢复。** 主 task 对同一 preflight 独立重跑：从未创建 run/attempt 时，live 复验通过后提交第一个 canonical request/run，不能称为 retry；已有 failed attempt 且全部 identity 不变时，才可显式 Workspace retry 创建新 attempt；任一 dataset/catalog/calendar/query/config/package identity 改变时，必须建新 snapshot 与 canonical request/run。复验失败发回原修复 task，不能新建重复任务；成功后可归档修复 task。

若线程工具不可用，明确向用户报告暂停和所需修复；不得将隐藏 subagent 描述成用户可见 task。

standing authorization 只覆盖遇到未来 MarketHub blocker 时创建可见修复 task，不需要每次重复询问。修复若需要凭据、数据许可、破坏性生产操作或改变研究范围，必须请求用户授权；在授权前主研究保持暂停，不得借此扩大权限。

### 研究暂停的呈现

主 task 可以继续完成不依赖数据的工作，例如来源冻结、规则审计、Package 结构和 fixture 测试；但所有带“正式”“收益”“通过”的结论都必须等待 live preflight 与 Nautilus 结果。向用户说明 blocker 时给出原 query、范围、故障分类、fingerprint 和当前暂停点，而不是笼统说“数据不可用”。

## Quant Runtime 形式化运行

先检查 Runtime 当前 CLI 与 request schema，不假设命令或 Apex Research 的 `study attach` 已存在于 main。将经过 preflight 的 canonical request 经 Workspace 公共合同提交。

- 数据探索仅用 Qlib；正式结果只由 NautilusTrader 的原生 orders、fills、accounts、positions、statistics 和 reporting 负责。
- 保留 run、attempt、snapshot、package hash、数据版本、catalog/calendar 版本、成本/保证金资料身份和原生 artifact。
- 失败先诊断根因。对相同身份只允许一个受控 retry；不得以旧结果、手工修正或非原生指标替代。
- 若对齐 legacy，只说明是否方向性复现及差异原因：数据、时间、调整、订单/成交、成本、账户或范围。绝不把历史数值复制成新结果。

### 运行质量门

完成 Runtime 阶段前，以 public CLI/API 复核 run/attempt 的终态、native event/row 合理性、artifact 可读取性与 snapshot 身份。数据可用性失败的 run 不进入 Research；运行失败不以 Reporting 的空模型掩盖。正式 report 使用的所有数值都必须回到同一个 completed run 的原生 reporting input。

## Apex Research 与研究边界

通过公共 Workspace 接口创建/复用 protocol、trial、evidence 和 decision。Apex Research 只连接已完成的正式 run；先查 CLI 是否支持 attach/发布，缺 capability 时提出最小、证据驱动的扩展方案。

研究记录必须固定：来源与规则映射、正式 run 身份、evidence gates、指标来源、数据/执行限制、legacy 差异和 decision scope。

`accept` 仅表示声明的证据门通过，不能等同 OOS、稳健性、容量或可实盘批准。没有原生支持或充分证据的归因、基准、敏感性、压力、容量、PIT 结论必须为 `not_evaluated`，详见 [证据与研究边界](references/quantresearch-evidence.md)。

## StrategyReporting 交付

只从正式 Reporting input 和 Apex Research 已发布记录构建 read-model。使用 Reporting CLI 的 `render-run`、`render-study`、`verify`、`rebuild` 与 portal；不得手写静态 HTML 或私造图表/指标。

面向人的中文报告应优先展示策略与状态、数据范围/完整性、净值和回撤、原生收益风险指标、交易/成本、已证实的归因、研究 decision、差异和限制。不要展示内部路径、命令、调试日志、重复 raw payload 或未被模型支持的“精确”结论。

在发布前按 [StrategyReporting 合同](references/strategy-reporting-contract.md) 做 deterministic rebuild、artifact 验证、portal 离线检查、桌面与 mobile 可视检查、console 无错误/警告检查。没有完整正式数据时可以生成明确标识为数据阻塞/研究设计的报告，但不可伪造图表或绩效。

报告内的配色、版式和文本均服务于信息层级：先显示研究身份、结果与风险，再展示解释和证据；避免密集监控面板、内部 JSON、过多装饰色或把不确定内容涂成确定结论。页面面向人而不是运维审计。

## 验收、提交与交付

每个 owning repo 在自身执行上下文完成写入。先跑最小相关测试，再跑要求的 lint/type/build/集成检查；不改无关用户变更。跨仓工作遵守 cross-project ownership，不直接写别仓。

提交按仓小而聚焦：检查 diff、精确暂存、检查 cached list、commit、正常 push（禁止 force push）。若没有 remote 或不具备 push 条件，明确说明而非声称已推送。

最终交付包含：冻结来源/候选证据、Package ID 与 revision、拓扑和关键参数、MarketHub 和数据身份、run/attempt/artifact、研究 decision、报告入口、测试、legacy 差异、限制、commit 与 push 状态。链接只指向真实公开记录或最终报告，不暴露私有数据库路径。

若任一正式环节没有完成，交付仍须明确标出停在哪一层、已经验证的非正式工作，以及恢复的唯一条件；不要把部分结构实现描述成已完成研究。

研究完成不等于合并到各仓 main；分别报告 feature branch、commit、remote 与 merge 状态。用户没有要求时，不主动合并或覆盖其他人的分支状态。
