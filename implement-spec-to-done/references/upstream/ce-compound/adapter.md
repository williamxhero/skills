# Local adapter — ce-compound capture only

**先完整读取本适配，再读取并执行 [instructions.md](instructions.md)，按当前分支读取全部必需参考。** 本适配优先于并贯穿所有加载的 upstream references、templates、脚本命令和 subagent prompts；原文件不直接修改。向每个新上下文内联本覆盖契约及其任务所需的完整上游合约。

来源：EveryInc/compound-engineering-plugin，commit `a3d6a8ee41a404b74391ee278423b52890b8920d`，`skills/ce-compound/SKILL.md`，SHA256 `D121FF53E4C78390FC6637CA989211489B7EC8AAA122F846E16F8938C9F73017`。39 个原文件／相对 scripts／assets 完整保留，根 MIT Every 2025 [LICENSE](LICENSE) 随目录交付。不可变逐文件路径／SHA256 见 [provenance.json](../provenance.json)；上游仅主文件改名为 instructions.md，字节不变。以下均是本地行为差异而非未修改上游执行。

## 模式、触发和写入边界（全局覆盖）

仅 capture `mode:non-interactive`；不接受 `mode:refresh`。自由文本即使包含 refresh 也仍 capture，不能进入 refresh／ce-setup／ce-plan／ce-work，不能启动 interactive enhancement、PR、commit、配置或 standards setup。Refresh 只可作为窄范围建议交给外层，不自动调用。本入口固定 `depth:full`；无 token 的上游默认也是 Full。显式要求 lightweight 须先由外层记录授权和减损，再读取 lightweight.md；不得因成本、能力故障或上下文不足静默降级。

一次只评估一个已经解决且验证过的错误认识（失败路线、真实审查缺陷、错误假设或文档）。调用方遇到合格触发 **必须调用技能判断**，不能先以“不值得写文档”省略。技能应用 durable counterfactual 后可 no-write；没有错误认识的正常任务不触发。不写流水账、不把 red 测试预期失败本身当成错误认识。

仅 orchestrator 可写 tracked 产物：form.md 允许的至多一个新增测试／已存在工具链的 lint 规则／既有 CODING_STANDARDS.md 条件，及至多一个 solutions learning，加上下述按需权威词汇更新。不得修改 product implementation code、创建 CODING_STANDARDS.md、改项目 instruction files、声明 pack、编辑 pack、改配置、开 PR 或提交。check 必须按真实 runner 在当前树 pass，并在 throwaway tree 独立破坏每个 guard 得到预期 fail；不能证明则撤销该 check，报告推荐并尝试下一 form。报告 gap／建议不是额外写权限。

嵌入资源不是公开技能命令：上游 report／refresh-and-discoverability 中可复制的 `/ce-compound refresh ...` 在本 bundle 不保证存在。将该字段输出为“Refresh recommendation: <scope> — 建议交外层另行授权处理（本次未执行）”，不生成虚假可运行命令，不因建议自动进入 refresh。

## 权威词汇替换（所有 reads／writes／reports／grounding）

先读取项目指令所指的权威 glossary，通常 `GLOSSARY.md`；与该领域相关的 ADR 同时约束决定。将 upstream **每一处 CONCEPTS.md 的读取、写入、副作用、seed、cross-reference、grounding 输入、discoverability 和 report 字段** 替换为该项目唯一权威来源，而不另建 CONCEPTS.md 或第二词表。如果项目明确以既有其他文档为唯一权威则沿用它；来源冲突／不明确先消歧，不创建副本。

仍无条件读取 concepts-vocabulary.md 的资格／coherence 规则，但取消其“例行创建／主动 seed”要求：只在实际出现需要记录的真实新术语时，读取并执行实际 domain-modeling，按项目惯例懒创建／更新 GLOSSARY.md 或指定权威。无术语就无新文件；不为了 capture 补齐整片 domain nouns。既有权威的相关 neighborhood 仅按本次证据作必要 add／refine／fold／scrub，不扩大全表审计，不凭未找到代码删除条目。遵守该权威本身格式而非强套 upstream preamble。

research 用权威相关词汇分类；assembly／lightweight 只按上述边界更新；grounding 新上下文接收并核验本次新增／修改的权威 entries；discoverability 仅评估现有权威，缺失不催建，gap 只报不改 instruction。最终 report 把 CONCEPTS.md 字段替换为 `Vocabulary authority: <actual path | absent>; <scanned/no qualifying terms | exact change counts>`，并报告其 discoverability。扫描无变化必须真是 byte-for-byte unchanged；记录每一写入，不掩盖副作用。

## Full 执行和真实平台能力

先读 [pi-execution.md](../../pi-execution.md) 并实际预检 Bash／Unix commands、Python 3、脚本绝对路径和工作目录；source-only 打包不证明任何命令可运行。每条上游命令的 `SKILL_DIR` 都是本 instructions.md 目录（文件名重命名的唯一锚点映射），保持内部相对资源不变。docs_root 只读 config.yaml，repo-contained／非根／非 .git 校验保持；packs resolver 独立可读两种配置，网络／cache 写入遵循授权与原 unresolved fallback。

Full 顺序保持：research（schema／yaml-schema、live corpus、独立 Related Docs Finder）→自动 history probe 与父级 drafting 并行→等待所有 Phase 1 输入→form／assembly／vocabulary→mechanical 和独立 semantic grounding→refresh recommendation／discoverability→report。必需参考按步骤全读，conditional historian 只在 probe relevance 达标后新进程；无相关 history 是正常输入，不是跳过 probe。Related Docs Finder／history synthesis／semantic grounding 均实际新上下文，用完整资产而非伪 API；沿用上游仅在明确失败条件允许的 fallback 并披露，不扩大为最终独立审查降级。

**Historian 唯一写入差异**：覆盖 references/agents/session-historian.md 的 `Never write any files` 为“只可写 caller 指定的本次 `{run_dir}/session-history.md` scratch artifact，确认存在且非空后仅返回路径；失败时按 caller 合约 inline 返回”。它仍只读取给定 extracted scratch，不读源 session，不写任何 tracked 路径，不运行 extraction／discovery／Skill。其余 scratch 安全、current-session 排除、技术内容／隐私边界保持。

每次终态必须是真实的 `Documentation complete`（有持久产物）或 `Documentation skipped` 加具体原因（无写入）；同时标明 Full、真实 grounding／history／packs 降级、权威词汇结果和全部写入。能力受阻如实交给外层，不以 skipped 文本冒充所需 Full 已完成。
