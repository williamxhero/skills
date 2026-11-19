---
name: implement-spec-to-done
description: "交付指定最终目标：先完成已授权前置，再返回单个／总 SPEC 或 ticket 的已核验 owning SPEC；唯一当前 SPEC 经 grilling、隔离实现、独立审查、新鲜验收及完整交付后关闭。"
---

# Implement Spec To Done

**逐个交付。** 原始提供的 Issue 是不可变的 **final delivery target**；先交付其已授权未完成前置，再返回原目标。全程只有一个 **CURRENT SPEC／当前 SPEC**；同一 SPEC 内按 ticket 图并行，跨 SPEC 不并行。下一 SPEC 连拆票也等待当前 SPEC 完整交付并回读 `CLOSED`；批准全部拆分不解除门禁。只在用户已授权实施或明确授权外层继续时进入；SPEC 发布不是开发授权。

每次进入先完整读取实际安装兄弟入口内的[共享工程规则](../to-spec-to-done/references/engineering-quality.md)和[自动推荐 grilling 适配](../to-spec-to-done/references/auto-grilling.md)。三入口是配套 bundle，CC Switch 必须同时安装兼容的 `to-spec-to-done`；只复制本目录不会带来兄弟资源，缺失即 blocked，不生成规则副本或猜全局路径。依共享规则从实际加载入口目录定位兄弟技能；显式提供 skill root 才使用该根。

技能调用是读取真实 SKILL.md／必需参考并执行，不是记忆流程或虚构 Skill API；此环境的 `disable-model-invocation` 不阻止委托，不修改上游。专项资源每次 **adapter 先于 instructions** 加载，适配贯穿其所有参考。真实角色与命令映射读 [Pi 执行适配](references/pi-execution.md)。记录实际版本／hash、路径、模式与差异；源码检查不是运行成功。

## B1 读取总需求，选唯一当前 SPEC

**选择 CURRENT 或任何新交付写入之前，必须完整读取并执行 [prerequisite delivery contract](references/prerequisite-delivery.md)**，包括单项、总项、ticket 及恢复／重复请求。该资源唯一维护完整闭包分类、安全门禁、选择、证据复用和返回规则；不能只凭入口摘要选项或拆票。

源头回读项目指令、tracker／标签规范、相关实现／测试、权威词汇／ADR 和相关经验，并依该合同读取完整相关闭包。完成条件：原 final delivery target 保留，整个闭包的来源／授权／安全核验成立，恰好一个可交付 CURRENT（或已核验 target no-op）；未解决的冲突／未知保持 blocked，不开始新交付写入。

## B2 提取完整完成方法，能力预检

完整读取当前 SPEC 和评论的验收、公开测试接口、完成方法／执行与交付原文。记录每条要求→实际操作→证据→关闭条件，含范围／非目标、必需技能及触发参考、branch／review baseline、合并／清理／push／tracker 要求。具体顺序按 SPEC，不用默认流程缩减契约。

在受阻写入开始前预检并留下结果：

- **技能**：共享兄弟资源；实际 `grilling`、`to-tickets`、`implement-spec`、`tdd`（含 tests.md／mocking.md）、`code-review` 的正文与必需参考可读。设计触发时预检 codebase-design／design-an-interface／domain-modeling，冲突时预检 resolving-merge-conflicts，以及 SPEC 额外依赖。
- **专项**：本目录 verification、simplify、compound 的 adapter／instructions／provenance／LICENSE 与所选分支资产可读、hash 对应版本。Full capture 的 Python／Bash 命令及路径按 Pi 执行适配实际探测；未触发不运行 capture，但不能把 source-only 当运行能力。
- **平台**：tracker 身份／权限、原生关系读写、Git 工作树／分支和正常 remote 交付能力，明确操作授权；读操作不能证明写权限。不可做安全探针的外部写入依授权并在实际阶段回读，不假造成功。
- **角色**：真实新上下文 implementer／merger／Standards／Spec／事实调查，以及条件触发专项 reviewer、Related Docs Finder、historian、grounding 能按各自工作目录／工具边界运行。按 Pi 执行适配做只读启动探针；启动故障不算委托，mandatory final review 不能退成自审。

完成条件：每项要求有可核验操作，必需能力／授权具备；否则 blocked，记录具体阶段、已查结果、保留成果和最小继续条件，不启动受阻写入。

## B3 每个当前 SPEC 拆票前必执行 grilling

完整读取真实 `grilling/SKILL.md` 并按已加载自动推荐适配执行一次实施级复核；即使简单、信息完整、已有 tickets 或规格级 grilling 也必跑，不对每个 ticket 重复访谈。复用已定决定和已授权公开 seam，集中核对现状、前序交付引起的设计漂移、状态／接口、迁移、失败恢复和实施风险。

每轮按 design tree 的 frontier：前置成立→调查事实→推荐→按既有授权自动采用本轮全部推荐→重算依赖。无机械轮数／问题数，不重复等常规确认；不可验证事实仍未知，自动推荐不扩权。模块／接口取舍按共享规则触发设计技能。范围、验收或 seam 有实质变化，先记录并按权限修订 SPEC／取得必要授权。完成条件：实施决定收敛，关键未知有处理，已有测试接口沿用；否则继续调查或如实 blocked。

## B4 调用原 to-tickets，核验复用

读取并执行实际兄弟 `to-tickets/SKILL.md`，只传当前 SPEC、既有决定／seam、交付门禁和自动推荐授权。沿其原模板拆可独立验证的 tracer-bullet 纵向 slices，声明阻塞 edges；不复制模板或另设计划主持器。沿用已授权的建议拆分，不重复常规批准；没有适用授权先取得它。

创建前搜索并回读既有 tickets，逐项核验需求覆盖、验收、身份和依赖，**复用**合格项，仅补缺。GitHub 使用当前 SPEC 的原生父子关系和原生阻塞关系及适用标签（默认 ready-for-agent），正文链接不代替 edges；本仓库所有 gh issue 操作显式 `--repo williamxhero/skills`，API 使用同一 repo endpoint。回读完整正文、父子集合及依赖方向，确认可执行 frontier；原生关系失败则 blocked。写入超时／未知先源头查结果，避免重复票／edges，不接管无关关联。

完成条件：已授权、实际发布、源头回读、覆盖及原生图完整。在此之前不编码，不先写代码再补票；本地 tracker 按其原文件／阻塞约定逐一验证。

## B5 调用 implement-spec，隔离实现与集成

完整读取真实 `implement-spec/SKILL.md`，传入 **仅当前 SPEC** 及回读后的 tickets、完成方法、关闭限制和以下阶段适配。建立／复用 SPEC 集成分支，固定并记录审查基点 SHA（当前任务起点，不随实现移动）、主干身份和所有资源归属。根据 tracker 图推进当前 SPEC 内的 frontier；实现已集成／验证是内部可执行前沿证据，不等于平台 ticket 已关闭，外层完成后统一关闭。

每个 ticket 使用新上下文 implementer 和独立工作树／分支，核对集成分支祖先／干净状态；基点不符安全合并或新建并保留成果，不破坏性 reset。读取实际 `tdd` 及 tests／mocking，按已授权 seam 一项行为→失败测试→确认预期原因→最小实现→确认 green；不批量预写全部测试，不为测试暴露内部。STATIC／模拟／实际模型／平台证据分别标记。

报告前 implementer 合入集成 tip，先读并执行 [verification adapter](references/upstream/verification-before-completion/adapter.md) 再执行其 instructions，提交候选、完整 red／green 输出及限制。新的 **merger** 上下文核验范围、提交／祖先、diff 和新鲜验证后正常合入集成分支；需要冲突解决则读取真实 resolving-merge-conflicts，保护 WIP。更新实际 ticket 图的可执行前沿再派下一批，不跨 SPEC。

### LOCAL PHASE ADAPTATION — 不是 hook API

显式传递本地执行顺序：保留 implement-spec 原步骤 1–6 的任务图、集成分支、fresh implementer／tdd／merger；全部当前 tickets 集成并稳定后，**插入 B6 简化 + B7 条件经验捕获，再进入原第 7 步 code-review（即 B8）**。原第 7 步只在此最终位置调用一次，不先跑一个旧候选的“最终审查”。修复按 B8 刷新受影响证据／必要复审，而非循环整个主持流程。

**原第 8–9 步的 ready／ticket close／报告及工作树 cleanup 全部暂缓**，由外层 B9 验收、B10 完整交付、B11 完成记录回读与关闭门禁释放；不能委托原技能的自动关闭／清理绕过。draft PR 依原技能可在首次合入后按授权创建，但暂不用可自动关闭 Issue 的 closing keywords／平台设置；推送、PR ready 或自动合并均不能提前释放关闭。无真实 hook 注册，不改 Pi 核心、不新增 scheduler state authority。

完成条件：全部当前 tickets 已合入集成分支、基线验证稳定，范围与证据明确；此时仍未完成 SPEC，不关 tickets、不清理必要工作树。

## B6 稳定后调用 ce-simplify-code mode:pipeline

先完整读取 [simplify adapter](references/upstream/ce-simplify-code/adapter.md)，再读取 instructions.md 和所有触发参考。输入 `mode:pipeline`、固定基点→当前候选精确差异、所有 proof-test 路径／证据及已确定 structure pins；范围仅当前 SPEC，不在每次 red→green 后整理。

按上游 preflight：prose／skills Markdown、配置、vendor／generated 不是代码；无代码或仅机械 churn 返回 `nothing to simplify`，不派 reviewer。混合范围的 authored Python 测试是代码，照常保留在范围。真实 reviewers 按完整 persona／Pi 进程适配执行；保护行为、安全、兼容和证明测试原样。简化后跑 typecheck／lint／blast-radius tests；未配置明确说明，失败则修复／撤销责任变更，不弱化断言。完成条件：新鲜检查支持行为保留，或有可核查 no-code／no-change 理由。

## B7 条件式单项 ce-compound 判断

核对已验证工作是否纠正错误认识。无触发记录 `not triggered`，直接 B8；有已解决且验证的错误认识，**必须**先读 [compound adapter](references/upstream/ce-compound/adapter.md) 再执行 instructions 及必需参考，由技能判断 cheapest durable form 或 no-write，调用方不能先以“不值得写文档”跳过。

固定 capture `mode:non-interactive depth:full`，一次一个问题，必要时顺序多次；自由文本含 refresh 不改变模式，绝不启动 refresh／enhancement／PR／config／standards setup。复用 GLOSSARY.md／项目指定权威，不并行养 CONCEPTS.md；全参考和子 Agent 使用相同术语／写入边界适配。Full 的相关文档、history 和 grounding 用真实新上下文及命令能力，不静默 lightweight。新增 check 要当前树 pass 且 throwaway 缺陷形状 fail（各 guard 单独证明），否则按技能撤销／推荐下一 form；词汇仅实际需要时懒维护。

完成条件：每个触发项确已判断，写入及影响均验证或有明确 no-write 原因；记录 Documentation complete／Documentation skipped 与具体证据。能力阻塞不能包装为已完成 capture，留在当前阶段。

## B8 最终独立 code-review（复用原第 7 步）

完整执行实际 `code-review`：解析固定基点与最终候选 SHA，确认 diff／commits，源头 SPEC 及所有规范（含共享工程规则）；范围涵盖实现＋简化＋经验产物。Standards／Spec 两轴并行、独立新上下文，前者传完整规范与 smell baseline，后者传原 SPEC；按原流程分别聚合，不自审、不合并排序轴、不用专项 inline 降级替代。

有发现交一个 fresh implementer 修复，按需 tdd／针对性简化／单项经验判断。变更使证据过期时重新验证受影响项，实质影响已审查结论时复审受影响差异；无实质变化不机械重复全套。不把旧审查报告套在新候选。完成条件：最终候选已审查，发现均修复或有符合验收的明确处置，证据绑定当前 tree／commit。

## B9 verification-before-completion 最终验收

先读 verification adapter 再执行 instructions；逐项运行当前 SPEC 的必需验收、真实验证、构建、检查和必要回归，读取完整输出／退出码并绑定候选与环境。架构 SPEC 的收益前后对照、兼容／迁移／回退也在关闭前验收。STATIC、模拟、真实 Pi／模型、平台回读、未运行分开记录；测试通过不推断全部需求满足。

完成条件：每一必需验收均有新鲜对应证据；未运行／失败是 pending 或 blocked，保留当前 SPEC，不推进交付关闭。

## B10 完整 Git 交付

按 SPEC 约定及权限，将集成成果正常合入主干，运行合并后验证；主干有新变更／冲突使候选结论过期则回到受影响验证／审查。核对每个待清理 branch 成果确实在主干、对应工作树干净且属于本任务，保留必要日志和恢复指针后才清理本次 worktree／已合并 branch。禁止丢 WIP、强推、强删未合并分支或处理其他线程资源，不默认调用全项目 commit-n-push。

按约定正常 push 主干，源头回读远端 ref，核对本地主干／tracking ref／真实远端 SHA 一致（如 git rev-parse、git ls-remote 对应 ref），不能只信 push 响应。清理／push 任一失败留住实际未交付状态，不关闭。

完成条件：主干集成、合并后验证、安全清理、push 和远端一致均有证据；这是 Git 交付，不代表 CC Switch 安装。CC Switch 仍是唯一安装／分发管理器，另需授权支持的导入／更新及实际验证，不改 SSOT／DB／config／客户端 links 绕过它。

## B11 完成记录回读，然后关闭

把当前 SPEC 的 tickets、提交、验收／审查、主干、清理、远端、适配版本及限制写入真实 tracker 完成记录，回读确认正文和证据一致。完整交付证据成立且记录回读通过后，先关闭 tickets，再关闭当前 SPEC，回读 `CLOSED`，最后更新总 SPEC todo 使正文／系统状态一致。本地 SPEC 写可核验完成记录才可推进。

误关／取消关闭／PR 自动关闭不是验收；发现未交付应按授权恢复真实状态并调查，不能借关闭结果跳过 B9–B10。外部写入结果未知先查源头，不盲重试。完成条件：全部原文要求满足、平台与证据一致；缺权限或失败保留成果，报告最小继续条件，不称 Done。

## B12 关闭确认后才选下一 SPEC

仅 B11 成立才切换 CURRENT。**每次关闭后、恢复及新事实出现时，必须重新完整读取并应用 [prerequisite delivery contract](references/prerequisite-delivery.md)**：源头刷新原 target 和整个剩余相关闭包／授权，重新核验并计算，再从 B2 继续，不依缓存顺序、不重复请求常规许可。第一项失败／未关闭，第二项不拆票、不编码。

按该合同复用真正 delivered prerequisites、最后返回原目标，并保留总体验收／独立集成及原 ticket 验收义务。报告真实完成／blocked 的身份、阶段、提交与证据；子票全关不能单独证明总需求完成。不自动递归架构扫描或开启下一轮重构。
