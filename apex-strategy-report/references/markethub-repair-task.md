# MarketHub 故障修复 task

## 触发和分类

当 health、HTTP、payload、历史覆盖、字段、日历、合约 catalog、版本、排序、重复、截断、PIT 或 time semantics 无法满足冻结请求时，先判断是否为：

- 标的上市前、交易所休市、策略明确排除或用户参数错误；修正研究请求或记录排除理由后重新 preflight。
- 真正 MarketHub 数据/服务/合同问题；立即走本流程。

不要把“其他标的存在数据”当作目标品种覆盖完成，也不要将不支持的字段近似替换为策略字段。

## 主研究暂停；修复 task 持续运行

主研究在确认真故障后立即暂停，不创建或重跑正式回测；这不暂停修复 task。只要仍有可执行 remediation，task 为 `active_remediation`；冻结 source plan 穷尽且唯一剩余动作需授权时为 `awaiting_authorization`；用户撤销或拒绝授权分别为非成功终态 `cancelled_by_user`、`blocked_by_denied_authorization`；只有原 query 与完整目标 universe live 验收通过才可 `repaired/completed`。一个 provider、运行环境、接口或凭证分支失败只会关闭该分支，绝不使 task final、idle 或宣称数据不可修复。

保留两层身份。`incident_key` 用于跨观测去重：以 package ID、revision、package hash、config digest、`base_url`/method/path、去掉 `data_version`、pagination、request-id 的规范化业务 query，以及 universe、fields、frequency、adjustment、start/end 的 canonical JSON/SHA-256 定义。只有这些研究/执行身份、universe、范围、字段、频率或 adjustment 改变才创建新 incident。

observation fingerprint 是 incident 的证据别名，以 canonical JSON/SHA-256 定义，固定包含：

- `base_url`、HTTP `method`、`path`、按 key/value 排序后的 `query`；
- 排序后的目标 `universe` 与 `fields`、`frequency`、`adjustment`、`start`、`end`；
- `dataset_version`、`catalog_version`、`calendar_version`、`error_class` 与 HTTP `status`。

对象 key 使用稳定排序，集合元素按稳定键排序，再以 UTF-8 canonical JSON 序列化并计算 SHA-256。排除时间戳、堆栈、易变 error message、请求 ID 与其他非确定性字段。另保存诊断证据：响应摘要/异常、PIT/time semantics、排序/重复/截断统计、Strategy Package ID/revision/hash、canonical request、run/attempt（如已有）和 coverage。

dataset/catalog/calendar、HTTP status 或 error 变化只产生新的 observation fingerprint alias，仍归同一个 incident/task。将 `incident_key` 写入 task 标题或首条 prompt，并将所有 observation alias 写入主 task 状态/研究 working evidence。`list_threads` 必须按 `incident_key` 查找；没有 Workspace run 时不得为了保存这些身份创建伪 run、attempt 或 research record。

生产查询只允许 `http://yosef-server:8803`；禁止 `localhost`、环回/本机地址、其他 host fallback。禁止缩短区间、删目标品种、使用本机数据/fixture/旧快照/旧指标，或重复启动相同 run。

## 创建或复用可见 task

用户已明确给出未来同类问题“无监督做下去”的授权。在用户已将 MarketHub/QuantResearch 研究置于范围内时，它覆盖创建/复用可见 task，以及非破坏、可回滚、owner-scoped 的代码/测试、正常 commit/push、带健康门/回滚的常规部署、来源真实且 staging/验证通过的窄范围幂等数据 upsert。该修复权限来自本次无人监督要求与当前研究范围，而不只是 `create_thread` 授权。

它不覆盖购买或升级订阅、新凭证/账号、绕过 paywall、许可不明的数据导出/留存/生产导入、全表替换/删除、不可逆迁移或扩大研究范围；这些必须转 `awaiting_authorization`。

1. 若 thread 工具可用，先 `list_threads`，按 `incident_key` 查找 active 或 `awaiting_authorization` 的 MarketHub 修复 task。
2. 命中同 incident 时复用该 task，以 `send_message_to_thread` 追加主研究身份与新的 observation fingerprint alias；不要创建重复 task。
3. 未命中才 `list_projects`，优先 QuantResearch saved project；随后用 `create_thread` 创建清晰命名的修复 task。涉及 Git repo 时使用默认 worktree。
4. 主研究使用 `wait_threads` 和 cursor 追踪，不通过反复 `read_thread` 或重跑获得“进度”。
5. 若 thread 工具不可用，明确告知用户主研究已暂停、所需 endpoint/query/范围与证据。隐藏 subagent 只能协助诊断，不能声称等同于用户可见 task。

## 修复 task 的完成条件

修复 task 按 QuantResearch 根与 MarketHub/QuoteMux 等目标仓的 `AGENTS.md` 和跨项目所有权规则工作。所有完成路径均需原始 endpoint/query 对完整目标 universe 的 live MarketHub 验收（字段、覆盖、日历/catalog/version、排序、重复和截断语义），并返回其 evidence。

- 若修改了代码、合同或部署：必须有相关 tests、精确 commit/push、deployment、health gate/rollback 和 before/after live evidence。
- 若为纯数据 repair：代码/test/commit/push/deployment 明确标记 `not_applicable`，不得制造 dummy commit；必须有 source lineage 与 entitlement、raw/staged artifact hashes、before/after audit、repair/capture/import IDs、新 dataset publication/version、health，以及原 query + universe live 验收。

“服务恢复”但原 query 仍缺数据不是完成。不要用本地测试替代远端验收；`awaiting_authorization`、`cancelled_by_user` 和 `blocked_by_denied_authorization` 均不是 repaired/completed。

## 无人监督修复 loop

每轮针对当前 residual（精确品种、字段、时段和合同）执行，保存每一个 probe 的 endpoint/source、代表键、覆盖、字段/单位/时间栅格、访问条件和失败原因：

1. 将可修的 owner 代码/合同/部署缺陷与真实数据缺口分开。前者照常在 owner repo 修复、测试、提交、部署并复验；数据许可受限不得取消这些工作。
2. 对完整目标 universe 继续审计并导出 residual；不得因第一个缺品种停止其余品种的审计。
3. 在 remediation 开始时冻结有限 `source_plan`：QuoteMux registry 中已配置且声明支持该事实的 providers；`/data` 中带 manifest/hash/lineage 的正式 capture；相关交易所/厂商的官方历史下载；当前工具可见且已获授权使用的研究平台；以及一次有界 web discovery（最多 4 个精确查询、每类最多 3 个可信候选，去重后冻结清单）。已选候选的官方文档新发现一个 route 时可追加一次并记录；不得无限扩展搜索。
4. 每个 candidate 只以一个精确代表键做 read-only probe，并先验证 access entitlement，以及导出/自动化、留存、再分发和生产导入权限。已有登录态或有效凭证不等于这些权限；不清楚则关闭该 route 并记录，不绕 paywall。
5. 清单中每个 candidate 终态后才可声称 source exhaustion：失败只关闭该 route，随后探测下一 route；成功 route 仍须满足既有事实合同。不得把“当前 provider 受限”或无界搜索替代该门。
6. 只在满足既有事实合同（身份、字段、单位、时间栅格、adjustment/PIT 语义和可保留 lineage）且许可明确时，进行有界获取、staging、导入和原 query live 验收。禁止合成、插值、分摊、旧 Apex、本机数据或 snapshot fallback。
7. 若路线是浏览器、网站、在线 Notebook 或长抓取，先调用 `$crawler`；若为 SuperMind，必须由 `$crawler` 转入 `$supermind-crawler`，并通过目标 owner task 写入，不由研究协调者跨仓直接修改。

只有 frozen `source_plan` 已逐项 probe 并记录终态，且剩余唯一安全动作确实需要新凭据、付费数据许可、破坏性生产操作或冻结研究范围变更时，才转 `awaiting_authorization` 并向用户说明最小授权。获得授权后复用同一 `incident_key` task 恢复 loop；不要新开平行 task。

若修复需要凭据、数据许可、破坏性生产操作或改变冻结的研究范围，修复 task 必须先达到上节 source exhaustion 门后才请求用户授权。授权前不得扩大权限，主研究继续保持暂停；协调者向用户呈现最小授权并保留 incident/task ID，不对 `awaiting_authorization` task busy-poll。授权后以 `send_message_to_thread` 向同一 task 发送授权信息以恢复，而不是将它归档或替换。

用户撤销研究时将 task 标为 `cancelled_by_user`；用户拒绝最小授权时标为 `blocked_by_denied_authorization`。两者均是非成功的研究/修复终态，绝不可称为 repaired 或 completed。用户日后重新授权时复用原 incident task 或明确 reopen，不保留僵尸 wait。

## 回到主研究

主研究收到结果后独立重跑完全相同的 preflight。若仍失败，或修复 task 未达 live 验收却提前结束，向原修复 task 发送新证据并要求恢复 `active_remediation`，不新建重复 task。通过后严格区分：

- preflight 失败且从未创建 run/attempt：提交第一个 canonical request/run，不能称为 retry；
- 将 Quant Runtime commit/build/adapter contract，以及 MarketHub/QuoteMux/provider release/build/API-contract revision 记入 evidence；服务修复前后 build 都必须记录。
- 已有 failed attempt，且仅服务实现修复、API contract/query semantics/dataset/catalog/calendar/config/package/Runtime executable identity 全部不变时：可显式 Workspace retry 创建新 attempt；
- 数据补齐通常改变 dataset；任一 dataset/catalog/calendar/query/config/package/Runtime executable identity 或 API/data semantics 改变时：创建新 snapshot 和新的 canonical request/run；
- 修复 task 的成果纳入研究 evidence；必要时归档该 task。

报告只呈现最终数据口径、完整性、限制和对结论的影响；endpoint 错误、修复日志和部署过程属于内部 evidence。
