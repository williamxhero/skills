# QuantResearch 架构

## 工程与写入所有权

默认工程根是 `D:\WILL\STOCK\QuantResearch`。每个仓库只修改自己拥有的文件；跨仓由协调 task 定义验收标准，目标仓自己的执行上下文完成写入，并先阅读自己的 `AGENTS.md`。公共交互一律通过 Strategy Workspace 的 CLI 或 `WorkspaceClient`，不能读 SQLite、私有 runtime 目录或私有 artifact 存储。

| 仓库 | Canonical owner | 典型交付 |
| --- | --- | --- |
| `strategy-workspace` | package、manifest、schema、contracts、run state、artifact 注册 | `strategies/<domain>/<package>/`、canonical request |
| `quant-runtime` | MarketHub 访问、Qlib discovery、Nautilus 正式执行 | frozen snapshot、native reporting input |
| `apex-research` | protocol、trial、gates、evidence、decision | research records、decision report source |
| `strategy-reporting` | presentation read-model 与 portal | deterministic HTML 与 offline portal |

## 研究拓扑

默认 `formal_only`：已冻结 package 直接进行 Nautilus 形式化回测。仅当问题确实要求候选生成/筛选时使用 `discovery_formal`；Qlib 的发现结果须作为候选证据，与 Nautilus 正式 run 清晰分离。

MarketHub 是唯一生产行情源，位于小电脑 `yosef-server`。正式 run 不能回退本机快照或 fixture。fixture 可验证 package、schema、事件桥接等结构，但不得生成形式化收益结论。

NautilusTrader 负责正式订单、成交、账户、持仓、统计及 reporting input。不得把旧 ApexTrade、手写撮合、手写账户或 Qlib 结果换名为正式执行。

## 开始前的发现顺序

1. 阅读各目标仓的 `AGENTS.md`、README、CLI `--help` 和最近同类 package。
2. 用公共 API/CLI 查询已有 package、request/run/attempt、study/decision 和 report；比较 package revision、参数、数据身份与范围。
3. 仅在身份不同或缺少必要对象时创建。不要假设某个可选 CLI（例如 attach completed run）已经合入 main。
4. 若发现公共 capability 缺口，先写出具体证据、影响和最小扩展面；实现只放 capability owner。

## 数据和身份

形式化请求必须显式冻结：instrument universe、频率、时间范围、adjustment、catalog、calendar、dataset/version、cost/margin/slippage profile、package hash 和参数。任何影响这些身份的改变都需要新的 canonical request/run；不得覆盖或解释为旧 run 的重试。

把 MarketHub 响应版本、snapshot、coverage 和版本语义记录到 run evidence。数据完整性与时间语义在正式回测之前闭环，而非在报告阶段补救。
