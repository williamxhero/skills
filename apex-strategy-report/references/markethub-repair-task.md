# MarketHub 故障修复 task

## 触发和分类

当 health、HTTP、payload、历史覆盖、字段、日历、合约 catalog、版本、排序、重复、截断、PIT 或 time semantics 无法满足冻结请求时，先判断是否为：

- 标的上市前、交易所休市、策略明确排除或用户参数错误；修正研究请求或记录排除理由后重新 preflight。
- 真正 MarketHub 数据/服务/合同问题；立即走本流程。

不要把“其他标的存在数据”当作目标品种覆盖完成，也不要将不支持的字段近似替换为策略字段。

## 主研究暂停；修复 task 持续运行

主研究在确认真故障后立即暂停，不创建或重跑正式回测；这不暂停修复 task。修复 task 从创建起处于 `active_remediation`，直到原 query 和目标 universe 的 live 验收通过。一个 provider、运行环境、接口或凭证分支失败只会关闭该分支，绝不使修复 task final、idle 或宣称数据不可修复。

fingerprint 以 canonical JSON 后的 SHA-256 定义，JSON 固定包含：

- `base_url`、HTTP `method`、`path`、按 key/value 排序后的 `query`；
- 排序后的目标 `universe` 与 `fields`、`frequency`、`adjustment`、`start`、`end`；
- `dataset_version`、`catalog_version`、`calendar_version`、`error_class` 与 HTTP `status`。

对象 key 使用稳定排序，集合元素按稳定键排序，再以 UTF-8 canonical JSON 序列化并计算 SHA-256。排除时间戳、堆栈、易变 error message、请求 ID 与其他非确定性字段。另保存诊断证据：响应摘要/异常、PIT/time semantics、排序/重复/截断统计、Strategy Package ID/revision/hash、canonical request、run/attempt（如已有）和 coverage。

将 fingerprint 写入主 task 状态/研究 working evidence，并写入 repair task 标题或首条 prompt，作为跨 task 去重键。没有 Workspace run 时不得为了保存 fingerprint 创建伪 run、attempt 或 research record。

生产查询只允许 `http://yosef-server:8803`；禁止 `localhost`、环回/本机地址、其他 host fallback。禁止缩短区间、删目标品种、使用本机数据/fixture/旧快照/旧指标，或重复启动相同 run。

## 创建或复用可见 task

用户已明确给出未来 MarketHub data blocker 的 standing authorization；它满足创建独立、用户可见 MarketHub 修复 task/thread 的显式 `create_thread` 授权条件，不需要逐次重复询问。该授权不覆盖凭据、数据许可、破坏性生产操作或研究范围变更。

1. 若 thread 工具可用，先 `list_threads`，按 fingerprint 查找 active MarketHub 修复 task。
2. 命中同 fingerprint 时复用该 task，以 `send_message_to_thread` 追加主研究身份与新证据；不要创建重复 task。
3. 未命中才 `list_projects`，优先 QuantResearch saved project；随后用 `create_thread` 创建清晰命名的修复 task。涉及 Git repo 时使用默认 worktree。
4. 主研究使用 `wait_threads` 和 cursor 追踪，不通过反复 `read_thread` 或重跑获得“进度”。
5. 若 thread 工具不可用，明确告知用户主研究已暂停、所需 endpoint/query/范围与证据。隐藏 subagent 只能协助诊断，不能声称等同于用户可见 task。

## 修复 task 的完成条件

修复 task 按 QuantResearch 根与 MarketHub/QuoteMux 等目标仓的 `AGENTS.md` 和跨项目所有权规则工作。它必须：

1. 在正确 owner 修复根因，增加相关测试；
2. 部署到小电脑 `yosef-server`；
3. 用原始 endpoint/query 在 live MarketHub 复验完整的字段、覆盖、日历/catalog/version、排序、重复与截断语义；
4. 返回 commit、push、deploy 及 dataset/catalog/calendar/coverage 证据。

“服务恢复”但原 query 仍缺数据不是完成。不要用本地测试替代远端验收。修复 task 只能在原 query 和整个目标 universe 完整通过 live 验收时完成；用户授权等待为 `awaiting_authorization`，不是完成。

## 无人监督修复 loop

每轮针对当前 residual（精确品种、字段、时段和合同）执行，保存每一个 probe 的 endpoint/source、代表键、覆盖、字段/单位/时间栅格、访问条件和失败原因：

1. 将可修的 owner 代码/合同/部署缺陷与真实数据缺口分开。前者照常在 owner repo 修复、测试、提交、部署并复验；数据许可受限不得取消这些工作。
2. 对完整目标 universe 继续审计并导出 residual；不得因第一个缺品种停止其余品种的审计。
3. 为每个 residual 枚举无需新增授权的可信 source-native 路线，不限于当前已安装 provider：已有 provider 和正式 capture、交易所或厂商的历史下载、provider-native archive、以及已有登录的研究平台。先用一个精确代表键 read-only probe；route 失败仅记录并关闭该 route，随后探测下一 route。
4. 只在满足既有事实合同（身份、字段、单位、时间栅格、adjustment/PIT 语义和可保留 lineage）时，进行有界获取、staging、导入和原 query live 验收。禁止合成、插值、分摊、旧 Apex、本机数据或 snapshot fallback。
5. 若路线是浏览器、网站、在线 Notebook 或长抓取，先调用 `$crawler`；若为 SuperMind，必须由 `$crawler` 转入 `$supermind-crawler`，并通过目标 owner task 写入，不由研究协调者跨仓直接修改。

只有已经逐项 probe 并记录了所有无需新授权的可信路线，且剩余唯一安全动作确实需要新凭据、付费数据许可、破坏性生产操作或冻结研究范围变更时，才转 `awaiting_authorization` 并向用户说明最小授权。禁止以“当前 provider 受限”替代 source exhaustion。获得授权后复用同一 fingerprint 修复 task 恢复 loop；不要新开平行 task。

若修复需要凭据、数据许可、破坏性生产操作或改变冻结的研究范围，修复 task 必须先达到上节 source exhaustion 门后才请求用户授权。授权前不得扩大权限，主研究继续保持暂停；协调者继续以 `wait_threads` 监控此 task，授权后向同一 task 发送授权信息以恢复，而不是将它归档或替换。

## 回到主研究

主研究收到结果后独立重跑完全相同的 preflight。若仍失败，或修复 task 未达 live 验收却提前结束，向原修复 task 发送新证据并要求恢复 `active_remediation`，不新建重复 task。通过后严格区分：

- preflight 失败且从未创建 run/attempt：提交第一个 canonical request/run，不能称为 retry；
- 已有 failed attempt 且 dataset/catalog/calendar/query/config/package identity 全部不变：可显式 Workspace retry 创建新 attempt；
- dataset/catalog/calendar/query/config/package identity 任一变化：创建新 snapshot 和新的 canonical request/run；
- 修复 task 的成果纳入研究 evidence；必要时归档该 task。

报告只呈现最终数据口径、完整性、限制和对结论的影响；endpoint 错误、修复日志和部署过程属于内部 evidence。
