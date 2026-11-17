# SPEC：整合 Pi 的规格、实施与架构改进三条 To-Done 工作流

**状态：发布正文；三流程整合待实施，验收项均未完成。本文件是发布快照，实际发布与关闭状态以目标 GitHub Issue 的回读为准。**

本规格由当前讨论通过已读取的 `to-spec-to-done` → `to-spec` 流程整理。原版 SPEC 模板保持不变，补入详细调用流程、验收和完整交付约定。用户已明确要求在 `D:/WILL/AGENT/skills` 创建独立源码仓库，目标 tracker 为 `https://github.com/williamxhero/skills/issues`。本轮只归档现有源码、保存第三个入口的未安装草案并发布 SPEC，不代表已经实施三流程整合。

## Problem Statement

用户希望 Pi 在开发前与开发中都能发挥资深工程师的设计作用：先理解事实与需求，设计简单直白、健壮、便于变化的模块，再把规格真正实施、验证、交付，并在适当时机简化和沉淀经验。

现有 `to-spec-to-done` 主要包装规格发布，`implement-spec-to-done` 已经管理逐个 SPEC 的拆票到交付关闭，但两者尚未形成统一的强制设计推敲、阶段性简化和经验回写约定。还没有 `improve-codebase-architecture-to-done` 将有证据的架构问题连接到同一条交付链。

直接全量启用 Matt Pocock、Superpowers、Compound Engineering 三套主持流程，会重复产生计划、状态、执行者、审查和收尾责任。需要保留一条主链，按阶段调用专项技能，并清楚区分已有能力、拟新增调用、本地适配和实际验证结果。

特别要求：`grilling` 不能因需求看起来简单或完整而省略。用户已授权逐轮自动采用全部推荐答案，目的是加强 Agent 对任务的思考和理解，而不是反复等待用户答题。

## Solution

保留三个清楚的用户入口：

- `to-spec-to-done`：调查真实环境，强制自动推荐模式 grilling，形成足够开工的设计，由唯一的 `to-spec` 入口生成并发布规格，回读核验后结束。
- `implement-spec-to-done`：始终只处理一个当前 SPEC，复核理解并按依赖拆票、实施，在实现稳定后简化、有条件沉淀经验，再完成最终独立审查、验收、集成、清理、推送和关闭。
- `improve-codebase-architecture-to-done`：有范围地发现架构摩擦，选择有证据且已授权的推荐候选，委托前两个入口完成规格与交付，再检查改进收益；不建立第三套实现器。

Matt 现有技能与两个 To-Done 包装入口继续负责主流程。Superpowers 提供经过明确适配的前期设计方法，以及真实的 `verification-before-completion` 专项调用。Compound 提供 `ce-simplify-code` 和 `ce-compound`，不启动第二条 `ce-plan` / `ce-work` 主链。

“调用”表示读取实际技能及触发的必需参考并执行；“本地适配调用”必须记录其与上游的差异；“参考方法”不冒充完整执行上游技能。流程图中的主持步骤由包装入口完成，不为了每个步骤另造一个技能。

## User Stories

1. As a Pi user, I want three明确的规格、实施和架构改进入口, so that 我不必记住三套彼此竞争的工作流。
2. As a Pi user, I want 每次生成规格前都执行 grilling, so that Agent 不能凭第一印象直接发布方案。
3. As a Pi user, I want grilling 自动采用每轮全部推荐答案, so that 推敲过程不消耗我的逐题确认时间。
4. As a Pi user, I want Agent 按决策依赖逐轮推敲, so that 下游设计建立在已经明确的前提上。
5. As a Pi user, I want 需求简单时仍执行比例适当的 grilling, so that 不跳过理解也不机械凑问题。
6. As a Pi user, I want Agent 自行调查能够查清的事实, so that 不把事实查询转嫁给我。
7. As a Pi user, I want 已有决定与明确约束被复用, so that 自动推荐不会反复推翻既定目标。
8. As a Pi user, I want 推荐、重要依据和未验证假设被简要记录, so that 我能审查决定而不阅读冗长内部思考。
9. As a Pi user, I want 自动推荐不扩大外部行动授权, so that 费用、发布、删除或其他不可逆操作仍守住边界。
10. As a Pi user, I want 模块、接口、状态权威来源和失败路径进入规格, so that 实施者知道应改哪里、怎样恢复以及怎样验证。
11. As a Pi user, I want 重要接口才运行 design-an-interface, so that 方案比较有价值而不是例行增加成本。
12. As a Pi user, I want 术语与真正重要的取舍按需维护, so that 文档保持准确而不过度膨胀。
13. As a Pi user, I want 只有 to-spec 生成并发布 SPEC, so that 包装入口不会维护另一套模板。
14. As a Pi user, I want 单个 SPEC 或总 SPEC 加子 SPEC 按真实验收边界选择, so that 不为凑清单强拆需求。
15. As a Pi user, I want 每个新 SPEC 都包含完整交付约定, so that 下游不能只交付代码或集成分支就宣称完成。
16. As a Pi user, I want 发布结果、标签和原生关系被回读, so that 草稿或正文链接不会被冒充平台中的真实结果。
17. As a Pi user, I want 规格入口单独调用时发布后停止, so that 我能控制何时开始实施。
18. As a Pi user, I want 实施入口始终只有一个当前 SPEC, so that 后续 SPEC 不会提前拆票或编码。
19. As a Pi user, I want 每个当前 SPEC 实施前重新核对理解与现实, so that 前序交付造成的设计漂移能被发现。
20. As a Pi user, I want tickets 沿纵向行为拆分并发布真实依赖, so that 实施能够沿可执行前沿推进。
21. As a Pi user, I want 已有合格 tickets 被复用, so that 重试或恢复不会重复建票。
22. As a Pi user, I want 实现者在隔离工作树中按约定公开接口 TDD, so that 并行写入和测试不会互相污染。
23. As a Pi user, I want 实现稳定后才做一次有范围简化, so that 整理不会打断每次红绿实现。
24. As a Pi user, I want 简化保护安全检查、行为和证明测试, so that 代码变短不会损害可靠性。
25. As a Pi user, I want 纯技能文档变更被正确识别为没有代码可简化, so that 不把 Markdown 误当作 ce-simplify-code 的可执行代码。
26. As a Pi user, I want 只有已经解决且验证过的错误认识触发经验捕获判断, so that 不会为每个任务制造流水账。
27. As a Pi user, I want 经验选择测试、检查、规范或学习说明中最有效的形式, so that 同类错误更难再次发生。
28. As a Pi user, I want 最终独立审查覆盖简化和经验回写后的最终差异, so that 旧审查结果不能替代新候选的验证。
29. As a Pi user, I want 审查修复后刷新受影响的验证证据, so that 子 Agent 的成功声明不能直接变成完成结论。
30. As a Pi user, I want 主干、清理、远端同步和 Issue 关闭都有证据, so that Done 表示真正交付而非中间状态。
31. As a Pi user, I want 架构扫描先限定范围并建立改进前证据, so that 重构针对真实摩擦而非理论美观。
32. As a Pi user, I want 架构候选经过比较后选择明确推荐, so that 不会默认实施扫描发现的一切。
33. As a Pi user, I want 没有值得实施的候选时正常结束, so that 持续设计不会变成持续改写结构。
34. As a Pi user, I want 架构改进复用已有规格和实施入口, so that 交付与任务状态仍然只有一套权威记录。
35. As a Pi user, I want 改进收益在关闭前成为验收证据, so that 完成重构不只是测试没有变红。
36. As a Pi user, I want 缺失技能、子 Agent 故障和外部未知被如实报告, so that 系统不会用近似手工步骤冒充真实技能执行。
37. As a Pi user, I want 上游版本、本地适配和安装范围可追溯, so that 后续更新不会悄悄恢复第二套主持流程。
38. As a Pi user, I want 中断恢复先核对实际 tracker、提交与外部结果, so that 不会重复写入或清理他人的未交付成果。
39. As a Pi user, I want 这三个入口的源码在我的独立 GitHub skills 仓库中版本化, so that 我可以审查、备份并跟踪改进而不上传整个技能库。
40. As a Pi user, I want CC Switch 继续负责安装和分发, so that 新源码仓库不会破坏既有客户端链接或形成第二套管理器。
41. As a Pi user, I want 不存在的第三个入口明确标为未安装草案, so that 源码归档不会被误认为功能已经生效。

## Implementation Decisions

### 1. 主流程、调用机制与授权

三个入口的阶段编排、完成条件和恢复责任归对应 To-Done 包装入口。公共规则只维护一份，入口通过明确的阶段指针加载；不复制完整上游模板，不新增第二份调度状态。

已有的 `to-spec`、`to-tickets`、`implement-spec`、`code-review` 和 `grilling` 保持可单独使用。默认先通过包装入口和明确标记的适配层实现整合；需要改变上游执行顺序或交互方式时，记录为本地适配，不虚称执行了未修改的完整上游流程。

本机忽略 `disable-model-invocation` 的全局规则继续适用；不以该字段为由拒绝子技能调用。业务授权、项目安全规则和用户明确暂停仍有效。

`grilling` 的自动推荐约定：

- 规格入口每次必调用；实施入口每个当前 SPEC 在拆票前必调用一次，聚焦理解复核、设计漂移和实施风险。
- 小任务也必须执行，但不设置问题数量、轮数或文档篇幅下限。
- 每轮只处理前置条件已成立的问题，调查事实后形成推荐，按用户授权自动采用全部推荐，再计算下一轮。
- 复用已经确定的决定；有新证据时修订决定和依赖，不为了形式重新访谈。
- 以授权替代逐轮等待答复及末尾再次确认，但不替代事实核查，不授权扩大范围、付费、破坏性操作或其他尚未许可的外部动作。
- 只保留可审查的结论、重要理由、假设和验证安排，不要求展示冗长内部思考过程。
- 单独调用原版 grilling 的交互方式不因这三个包装入口而被全局修改。

### 2. 设计规则与事实来源

在与风险相称的深度上检查目标与非目标、真实约束、模块职责、接口调用成本、状态权威来源与写入责任、关键成功及失败路径、幂等与恢复、兼容迁移、可观测性、测试与验收。

新增抽象、服务、队列、缓存或插件机制必须对应实际问题，比较更简单的做法，写明暂缓机制及需要升级设计的触发条件。不将吞吐扩展与新增能力的可修改性混为一谈。

重要接口形状存在真实取舍时才调用 `design-an-interface`。使用 `codebase-design` 判断接口深度、复杂度封装和修改局部性；不把设计质量等同于文件数量或图的层数。

既有术语和 ADR 必须尊重。术语改变或新的重要取舍才调用 `domain-modeling`，按需写入相应权威记录；不在任务进行中平行建立相互冲突的术语库。

### 3. 流程 A：to-spec-to-done

```text
用户调用 to-spec-to-done（需求或讨论结果）
        ↓
A1 读取需求、代码与真实环境
   主持：to-spec-to-done
   操作：读取项目指令、相关实现、既有 SPEC、术语、ADR 和相关经验
   输出：现状、变化范围、事实、已有决定、假设、未知
   门禁：能够说明现在怎样工作以及本次改变什么
        ↓
A2 强制调用 grilling〔自动推荐模式的本地适配〕
   每轮：找出可解决的问题 → 核实事实 → 给出推荐
         → 自动采用全部推荐 → 更新决定 → 推进下一轮
   内部按需调用：
     codebase-design      模块、接口、复杂度与测试接口的设计参考
     design-an-interface  重要接口存在真正不同方案时比较并采用推荐
     domain-modeling      术语改变或重要取舍成立时维护记录
   门禁：影响当前规格的设计问题收敛，重要未知有处理方式
   不满足：继续本轮事实调查或下一轮推敲，不等待常规选择确认
        ↓
A3 检查是否足够开工
   主持：to-spec-to-done
   参考：共享工程规则及经本地适配的 Superpowers 设计方法
   检查：目标、非目标、职责、接口、状态、失败处理、测试与验收
   门禁：没有阻止首个可验收切片实施的未决事项
   不满足：回到 A2；不可查明的关键事实保留为真实阻塞
        ↓
A4 选择规格组织和交付约定
   主持：to-spec-to-done
   单个清楚验收范围 → 单 SPEC
   真正独立目标或验收边界 → 子 SPEC + 总 SPEC
   门禁：依赖、总体验收、每个新 SPEC 的执行与交付要求明确
   此阶段不拆实现 tickets
        ↓
A5 调用 to-spec〔唯一的规格生成和发布入口〕
   输入：原始需求、grilling 决定、假设、测试接口、规格组织及交付要求
   操作：必要补查 → 使用原模板 → 发布或复用规格 → 设置适用标签
   确认：沿用已授权的推荐和测试接口，不重新发起同一轮确认
   门禁：规格在实际 tracker 中存在，不能只生成聊天文本或本地草稿
        ↓
A6 回读核验
   主持：to-spec-to-done；使用 tracker 工具、CLI 或 API
   核对：正文、验收、完整交付段落、关键设计结论
         总清单、原生父子关系、原生阻塞关系、幂等复用
   不完整：在授权内修复后回读；无权限则保留成果并报告未完成
        ↓
A7 报告并停止
   Done：规格发布、标签及必需关系核验通过
   不调用 to-tickets，不编码，不执行实现阶段的 Git 交付
```

设计结果放回原模板的实施决定、测试决定、非目标和进一步说明。除必需的技能执行入口链接和原型决策例外，不在规格与 tickets 中固化具体实现路径或大量代码；当次文件定位和精确命令放进绑定提交版本的执行记录。

沿用原有遗留兼容规则：已有明确拆票结果的遗留 SPEC 复用原 tickets 及依赖，不为本次规则强制补交付段落或重新拆票；有总 SPEC 时仍核对其清单与关系。

### 4. 流程 B：implement-spec-to-done

```text
用户调用 implement-spec-to-done（单 SPEC 或总 SPEC）
        ↓
B1 读取总需求，选定唯一当前 SPEC
   主持：implement-spec-to-done
   操作：回读正文、评论、父子关系、依赖及既有交付证据
   总 SPEC：只整理子 SPEC 标识、顺序和前置条件，不提前拆后续票
   门禁：当前 SPEC 唯一，前置 SPEC 已交付并关闭
        ↓
B2 提取当前 SPEC 的完整完成方法并做能力预检
   主持：implement-spec-to-done
   记录：范围、验收、测试接口、必需技能、分支、交付、清理、关闭条件
   预检：技能及参考可读，tracker 可用，真实子 Agent 能按角色和工作目录运行
   门禁：每项要求有可核验操作；必需能力不满足则不启动受阻的写入
        ↓
B3 强制调用 grilling〔自动推荐模式的本地适配〕
   目的：复核当前 SPEC 的理解、现有实现与计划是否一致
   每轮：识别实施问题 → 查事实 → 推荐 → 全部自动采纳 → 推进依赖
   按需调用：codebase-design / design-an-interface / domain-modeling
   检查：前序交付是否改变接口、状态、测试方式、迁移和主要风险
   门禁：实施决定清楚，测试接口沿用已确定结论
   范围或验收契约发生实质变化：先记录并按授权修订规格
        ↓
B4 调用 to-tickets〔当前 SPEC 范围内〕
   操作：纵向切片 → 明确阻塞依赖 → 复用合格旧票 → 发布补齐项
   确认：在自动推荐授权内采用建议拆分，不重复等待常规批准
   核对：需求覆盖、验收、原生父子关系、原生阻塞关系及可执行前沿
   门禁：tickets 真实发布并回读；未达到时不编码
        ↓
B5 调用 implement-spec〔传入本地阶段编排与交付门禁〕
   输入：仅当前 SPEC 及其已发布 tickets、验收与关闭限制
   建立 SPEC 集成分支并记录固定审查基点
        │
        ├─ 对每个依赖已满足的 ticket：
        │    新上下文 implementer + 独立工作树
        │          ↓
        │    调用 tdd
        │    逐行为：失败测试 → 确认失败原因 → 最小实现 → 确认通过
        │    通过预先约定的公开接口验证，不为测试泄露内部实现
        │          ↓
        │    调用 verification-before-completion
        │    绑定实际候选与环境，运行并读取验证，不信任口头成功
        │          ↓
        │    merger 核验并合入 SPEC 集成分支
        │    必要时调用 resolving-merge-conflicts；不能覆盖未保存成果
        │          ↓
        │    更新当前 SPEC 的 ticket 依赖前沿，再派发下一批
        │
        └─ 仅当前 SPEC 内允许按依赖并行；跨 SPEC 不并行
   门禁：全部当前 tickets 已集成，实现与基线验证稳定
   此时不得自动关闭 tickets 或 SPEC，不提前清理必要工作树
        ↓
B6 调用 ce-simplify-code〔mode:pipeline〕
   位置：implement-spec 原最终 code-review 之前，不在每次编辑后执行
   输入：当前 SPEC 的精确差异、范围、证明测试及已确定的结构约束
   操作：有范围地简化 → 保护行为、安全和证明测试 → 重新运行检查
   纯文档或仅机械变化：按技能规则返回 nothing to simplify，不伪造修改
   门禁：简化后验证成立，或有证据说明无需简化
        ↓
B7 检查已验证工作是否纠正过错误认识
   主持：implement-spec-to-done / 当前集成负责人
   未触发：记录未触发，直接进入 B8
   触发：调用 ce-compound〔capture，mode:non-interactive，本地术语适配〕
         一次处理一个已解决且验证的经验，由该技能判断是否值得持久化
         优先有效的测试、检查或规范；必要时写经验，不强制新建文档
         复用项目权威术语，不另养一份冲突词表
   新增测试、检查或规范后：重新核验受影响结果
   门禁：触发项已评估并验证其产物，或明确无内容值得写入
        ↓
B8 调用 code-review〔复用 implement-spec 原最终审查位置〕
   固定基点与候选；共享工程规则纳入适用规范来源
   并行独立子 Agent：Standards 轴 / Spec 轴
   覆盖：实现 + 简化 + 经验回写形成的最终差异
   发现问题：新上下文 implementer 修复，按需 tdd / 针对性简化 / 经验捕获
             重新核验受影响结果，必要时复审；不把旧审查算到新候选
   门禁：问题已修复或有符合验收的明确处置，当前候选已审查
        ↓
B9 调用 verification-before-completion〔最终候选验收〕
   运行 SPEC 必需验收、真实验证、构建、检查及必要回归
   核对每项验收证据；模拟、真实、未运行分别标记
   架构改进 SPEC：此处必须验证改进前后收益和兼容性
   门禁：最终候选的全部必需项满足，不能由测试通过推断全部需求满足
        ↓
B10 按当前 SPEC 约定完成 Git 交付
   主持：implement-spec-to-done；merger 按约定集成
   合入主干 → 验证合并结果 → 核实成果归属及无未保存改动
            → 保留证据 → 清理本次工作树和已合并分支
            → push 主干 → 回读远端提交并核验一致
   必要时调用 resolving-merge-conflicts，不默认委托全项目 commit-n-push
   门禁：不强推、不丢弃 WIP、不清理他人或未交付资源
        ↓
B11 写入完成记录，回读后关闭
   主持：implement-spec-to-done；使用真实 tracker 能力
   记录：tickets、提交、验收、审查、主干、清理、远端及限制
   顺序：交付证据成立 → 完成记录回读 → 关闭 tickets → 关闭当前 SPEC
         → 回读 CLOSED → 同步总 SPEC 的 todo 项
   门禁：平台状态与证据一致；本地 SPEC 使用可核验完成记录
        ↓
B12 关闭确认后才进入下一 SPEC
   刷新依赖 → 选择下一个当前 SPEC → 从 B2 继续
   所有子 SPEC 完成后核对总体验收
   尚有独立集成工作：以总 SPEC 为唯一当前 SPEC，再走同一闭环
   全部完成：写总记录、关闭总 SPEC、回读后报告
```

这不是新增运行时 hook API。调用 `implement-spec` 时必须传入明确阶段契约：其全部 tickets 集成后，先执行 B6–B7，再进入原第 7 步最终审查；原第 8–9 步的关闭、资源清理和报告受外层交付门禁约束。实现者能核对实际顺序；不能只在文档中声称已经插入一个不存在的 hook。

审查、验收和证据绑定具体候选。修复或新增产物使相关结论过期时更新它们；无实质变化时不机械重复完整审查。审查发现新的已验证经验可触发新的单项捕获，但不得成为无边界的整理循环。

### 5. 流程 C：improve-codebase-architecture-to-done

```text
用户调用 improve-codebase-architecture-to-done（范围或痛点）
        ↓
C1 确定扫描范围、授权与基线
   主持：improve-codebase-architecture-to-done
   用户指定范围优先；否则依据近期变更热点限定调查
   读取项目指令、术语、ADR、历史经验、当前实现及测试
   门禁：目标范围清楚，扫描不等于任意范围重构的授权
        ↓
C2 调用 improve-codebase-architecture〔候选阶段的本地适配〕
   委托：原技能的调查与候选报告阶段；暂不独立执行其 grilling 阶段
   内部调用：codebase-design + 新上下文探索子 Agent
   调查：浅模块、修改分散、接口泄漏、状态责任、测试困难等真实摩擦
   应用删除测试，区分理论偏好与实际成本
   输出：涉及模块、证据、问题、收益、前后示意、推荐强度与首选
   门禁：候选有现实依据；此时不提前确定完整新接口
        ↓
C3 选择本次推荐候选
   主持：improve-codebase-architecture-to-done
   在已授权范围内自动采用有证据、收益明确、风险可控的首选
   默认只推进一个候选或不可分的一组，不实施报告中的全部建议
   无值得实施的候选：报告 no-change，并以无代码变更结果正常结束
   关键事实未明或越过授权：报告具体阻塞，不伪装为 no-change
        ↓
C4 调用 to-spec-to-done〔输入选中候选、证据与改进目标〕
   由该入口强制调用 grilling〔自动采用每轮全部推荐〕
   内部按需调用：codebase-design / design-an-interface / domain-modeling
   推敲：保留行为、状态责任、接口收敛、迁移顺序、失败恢复与测试
   事先确定可核验的收益，不以文件数、行数或层数作为通用成功指标
   再调用 to-spec 发布改进 SPEC，回读正文、关系及交付要求
   门禁：发布核验通过，且收益、兼容与回退要求进入验收
        ↓
C5 接收规格结果，确认实施范围未漂移
   主持：improve-codebase-architecture-to-done
   to-spec-to-done 在此已按自身语义结束；外层继续已授权的总任务
   不重建 SPEC、不复制 tickets、不另设执行计划和任务状态
   门禁：真实 SPEC 引用明确，阶段交接与后续行动均在原授权内
        ↓
C6 调用 implement-spec-to-done〔仅传入本次改进 SPEC〕
   复用流程 B：当前 SPEC → 强制 grilling 复核 → to-tickets
               → implement-spec / tdd / verification-before-completion
               → ce-simplify-code → 条件式 ce-compound
               → code-review → 最终验收 → 合并、清理、push、关闭
   改进收益的验证必须发生在 SPEC 关闭前，不留到外层事后补验收
   门禁：完整交付与关闭证据成立；失败留在当前 SPEC，不换候选掩盖
        ↓
C7 回读交付与收益，报告本次结果
   主持：improve-codebase-architecture-to-done
   回读主干、远端、tracker 和交付前已形成的改进收益证据
   候选或环境变化使证据过期：恢复对应验证与真实状态，不谎称完成
   Done：所选改进已验证并交付，或 C3 有依据地判定无需改进
   不自动再次扫描并启动下一轮重构
```

原架构技能在候选选择后已有 grilling 阶段。新入口通过明确的候选阶段适配将这段设计工作交给 `to-spec-to-done` 中唯一的规格级 grilling，不先完整跑一次再无条件重复一次。实施入口 B3 是对实际实施环境的复核，复用上述决定，不重新发明需求。

单独调用原 `improve-codebase-architecture` 仍可保持原交互方式。三入口设计采用自动推荐规则，不将本地阶段适配报告成完整原版执行。

架构报告可以使用临时 HTML；如果委托 visual-explainer，则按该工具要求先 prepare 获取目录，再写源文件并 render。图应标明现状与拟议结构，不能把拟议接口标成已经存在。

### 6. 专项依赖与本地适配

必需能力包括已有 Matt 技能及其触发的参考、Superpowers 的 `verification-before-completion`、Compound 的 `ce-simplify-code` 和 `ce-compound` 及其参考资源。后两者分别在稳定后的简化检查点和经验触发点执行，不扩大到 CE 的第二套规划和实现链。

依赖按可追溯版本选择性安装或提供经过审查的适配资源，不默认全量启用 `using-superpowers`、另一套全局 bootstrap 或自动收尾。完整参考资产必须可用，不能只复制一个 SKILL 正文就声称依赖已齐。

Superpowers 的 brainstorming / writing-plans 仅作为设计和计划质量的方法来源，不完整调用其中的第二套批准、文档、执行器交接流程；不能报告成已经执行这两个原版技能。

CE 经验捕获使用明确的 non-interactive capture。项目已有权威术语来源时，本地适配将词汇副作用交给该来源，不再平行维护冲突词表。适配差异必须可见，保持技能对经验资格、写入范围与验证的约束。

技能文件不存在或不可读、必需子 Agent 无法运行、tracker 关系无法建立时，保留具体阶段和已有成果，报告阻塞。不以人工近似替代必需调用而宣称成功。上游允许降级的专项步骤仅按其真实规则降级并记录；必需独立审查不能伪装成同一 Agent 的自审。

### 7. 状态、文档、恢复与范围

SPEC 是目标、验收和确定设计的权威记录；tickets 和原生依赖是实现任务图；代码与配置描述实际机制；术语、ADR 和经验各有明确职责。执行记录保存阶段、版本、指针与证据，不成为第二个任务调度器。

恢复执行前读取实际 tracker、工作树、分支、提交和远端状态，核验外部写入结果，再决定是否重试。只处理本次创建或明确归属当前任务的资源，不把用户其他线程成果一并提交、合并或删除。

用户已授权独立源码仓库 `williamxhero/skills`，本地为 `D:/WILL/AGENT/skills`；该仓库仅收录三个入口及本仓库必要文档，不导出整个 CC Switch 技能库。CC Switch 继续管理 `~/.cc-switch/skills/` 和原有客户端分发／链接。本轮将前两个已安装入口逐文件原样导出，把此前不存在的第三个入口作为未安装草案归档，不改写 CC Switch 配置、现有技能源或 Pi 链接。

后续开发在独立仓库形成可审查版本，再通过经授权、受支持的 CC Switch 导入／更新流程管理安装并核验实际客户端内容；Git pull 或 push 不等于部署。禁止建立另一套自动同步或把客户端链接重定向到本仓库来绕过 CC Switch。任何新增安装、依赖、共享根变更均需保留备份、适用授权和恢复路径。

三个原生技能命令是确定性入口；如提供短命令，只允许薄转交模板，不复制阶段正文。资源加载、同名冲突、符号链接与 reload 后发现情况需要真实验证。

## Testing Decisions

### 测试接口与方法

测试面是三个用户入口及其可观察的委托次序、持久化记录和完成状态，不测试隐藏实现细节。通过 Pi 原生技能命令和真实技能加载记录确认调用；必要的外部平台和 Git 验证在隔离测试仓库进行，不在用户真实应用上制造试验性副作用。

优先使用既有测试、tracker 和工具链。静态链接与字段检查只能证明结构，不证明模型真的按流程执行；模拟调用、真实 Pi 路径、真实子 Agent、真实平台回读分别标注。行为评估记录采用的技能版本、候选提交、环境、输入、调用证据和外部结果。

本轮已读取原有 To-Done、to-spec、to-tickets、implement-spec、TDD、双轴审查和架构调查流程作为先例。后续实施优先复用这些公开阶段，而非建立新的大型评估框架。

### 验收标准

- [ ] 三个入口在 Pi 中实际可发现、可读取、可执行；不存在同名冲突或失效的共享规则指针。
- [ ] 普通需求、信息完整的简单需求都实际执行规格级 grilling；没有基于任务简单而跳过的路径。
- [ ] 自动推荐按依赖逐轮推进，不等待常规逐题答复，不设置机械问题数量下限；关键决定进入规格。
- [ ] 能查清的事实先调查；无法验证的事实明确标注，不用推荐答案伪造事实或授权。
- [ ] 单独原版 grilling 和原架构调查仍保持自己的可用交互入口，本地自动适配的差异有记录。
- [ ] 规格只由 to-spec 按原模板生成；每个新 SPEC 包含本规格末尾完整交付段落。
- [ ] 单 SPEC、总 SPEC 加子 SPEC 两种组织均有真实发布和回读证据；GitHub 原生关系和正文清单一致。
- [ ] 重复执行规格发布不会重复建票或建立无关关系；遗留已有 tickets 的兼容规则得到保留。
- [ ] tracker 不可达或关系无权创建时明确未完成；本地草稿不被报告为发布成功。
- [ ] 单独规格入口发布后停止，不启动拆票、编码、Git 集成或推送。
- [ ] 多 SPEC 场景中任一时刻只有一个当前 SPEC；后续 SPEC 的拆票也等待前项交付关闭。
- [ ] 每个当前 SPEC 在拆票前完成实施级 grilling 复核，复用既有决定，不把每个 ticket 变成重复访谈。
- [ ] tickets 真实发布、依赖正确、已有合格项复用；没有先编码后补票的成功路径。
- [ ] implementer、merger 和独立双轴审查使用支持的真实子 Agent 机制；独立工作目录和阶段结果可核验。
- [ ] TDD 通过约定公开接口逐项红绿推进；已有测试接口授权被复用，新实质变更有记录。
- [ ] 稳定后的简化在最终 code-review 之前实际发生；纯技能 Markdown 变更按上游规则返回无代码可简化，不启动无意义审查。
- [ ] 简化不删除或弱化证明测试、安全、兼容或已确定结构约束；失败时修复或撤销责任变更，不削弱断言。
- [ ] 只有已解决且验证的错误认识触发 ce-compound；触发后由技能判断载体或无需写入，不能调用方先以不值得写文档为由省略。
- [ ] 经验捕获明确 non-interactive、一次一个经验，不意外启动 refresh 或另开 PR，不平行维护冲突词表。
- [ ] 最终 Standards / Spec 独立审查覆盖简化与经验回写后的差异；实质修复使相关证据失效后会更新验证或复审。
- [ ] verification-before-completion 在完成声明前使用真实命令与输出；构建、测试通过、需求满足、远端同步各有对应证据。
- [ ] 主干集成、合并后验证、安全清理、push、远端核验成立后才记录并关闭；子技能、PR 自动关闭不能绕过门禁。
- [ ] 架构候选有实际代码或运行依据、扫描范围和改进前基线；不把图形美观或减少文件数当作普遍收益。
- [ ] 架构入口只推进本次推荐范围，委托同一规格和实施链；无候选时正常 no-change，事实或授权不足时明确 blocked。
- [ ] 改进收益、兼容、恢复和迁移要求在关闭前已验收，外层回读不能替代缺失的交付前证明。
- [ ] 三入口不会自动递归调用架构改进、重扫全仓或在已交付后不断追加重构。
- [ ] 中断恢复不重复外部写入、不误关未交付 Issue、不清理未合并成果；完成记录能够重建当前 SPEC 和证据。
- [ ] 上游依赖版本、适配差异、共享目录影响及 Pi 加载方式可追溯；缺少必需依赖时不能报告真实端到端通过。
- [ ] 三入口源码在用户指定仓库版本化；现有入口初始导出有逐文件哈希，第三入口的草案状态可见；后续部署仍经 CC Switch 管理，不改用另一套安装器或客户端直连仓库。
- [ ] 本轮归档与后续实施的成果、未完成验收和实际安装状态分别记录；没有把仓库创建或 Issue 发布误记为三流程整合已经完成。

### 必测情景

1. 小而明确的需求：仍执行 grilling，自动采用推荐后由 to-spec 发布，再停止。
2. 多轮依赖与新事实：下游问题等前置决定；新事实推翻推荐后更新决定，而不坚持旧答案。
3. 两个顺序子 SPEC：第一项失败或未关闭时，第二项不拆票、不编码。
4. 已有 tickets 的恢复：复用任务图，回读外部结果，重复执行不产生重复票据。
5. 简化后回归失败：恢复或修复对应变更，保留证明测试，不以原来通过的测试报告完成。
6. 错误认识经修复并验证：调用 ce-compound 判断；没有适格经验时不生成装饰性文档。
7. 审查修复改变最终候选：验证和审查覆盖最终差异，不重复完整审查无变化内容。
8. 无收益架构候选：有依据地 no-change；有收益候选则先规格后实施，收益在关闭前验证。
9. 缺失必需技能或子 Agent 启动失败：停留真实阶段，不声称已运行独立审查。
10. tracker 不可达、关系失败或外部结果未知：保留草稿或已发布成果，源头回读后再决定重试。
11. 用户已有无关 WIP 与其他线程分支：不得纳入本次自动提交、清理或全项目交付。
12. 本规格本身是技能文档工作：ce-simplify-code 正确判断非代码范围，技能行为仍经独立流程验证。

## Out of Scope

- 本轮部署或启用增强技能、第三个草案、专项依赖或全局 bootstrap；本轮只创建源码归档仓库、保存草案和发布 SPEC。
- 在规格发布阶段拆实现 tickets、实施三流程增强、创建实现工作树或关闭实施 Issue。用户已授权的归档仓库初始化、源码提交与正常推送属于本轮操作，不代表执行本 SPEC 的实现交付。
- 同时启动 Superpowers 和 CE 的完整规划、执行、任务状态与收尾主流程。
- 为每个小任务建立固定篇幅设计文档、固定数量问题或完整多 Agent 审查团队。
- 无授权的全仓重构、自动循环架构扫描、修改产品目标、付费或其他外部承诺。
- 全局修改单独原版技能的交互行为，或修改 Pi 核心来假装存在一个阶段 hook。
- 借本次任务修复所有既有子 Agent / CLI / 其他项目问题；只有阻碍所需流程的能力就绪才属于后续必要前置。
- 改变其他项目的 Git remote、修改 CC Switch 配置或扩大同步到其他线程成果。仅用户明确授权的新 skills 仓库可在本轮创建、提交源码并发布 Issue。

## Further Notes

### 当前归档与发布约定

- 用户已明确指定新目标 `williamxhero/skills` 和本地源码目录 `D:/WILL/AGENT/skills`；新库按私有仓库创建并启用 Issues，未要求公开现有本地技能。
- 新仓库只保存三个入口及必要仓库文档。前两个入口从 CC Switch 原样导出，包含代理元数据并有历史文件哈希；第三个入口此前不存在，现仅保存未安装、未验收的源码草案。
- CC Switch 管理目录和 Pi 现有符号链接保持不变。新仓库是版本化源码归档，不是第二个技能管理器。
- GitHub SPEC 是当前任务的权威记录；本文件是发布快照。标签使用 `ready-for-agent`，创建及最终结果必须源头回读，不能只凭本文件状态宣称成功。
- verification-before-completion、ce-simplify-code、ce-compound 及三入口整合仍是后续交付能力；归档的旧入口不因 SPEC 发布而自动具有新增流程。
- 前期对子 Agent 的只读调查尝试在启动前失败：CLI 把 `--no-session` 解析为 `-p/--port` 的值。后续必须重新核验需要的执行能力，不能把该次失败记为成功。
- 原草稿位于另一个工作区且被其忽略规则排除；本轮在用户指定的新仓库保存发布快照，不改原项目的 gitignore、远端或无关 WIP。

### 自动推荐的结论记录

第一轮采用：保留现有 To-Done 主链；grilling 必跑并自动采用推荐；三个入口作为一个整合 SPEC 描述，不为了对应三个命令强拆子 SPEC。

第二轮采用：实施入口每个当前 SPEC 复核一次 grilling；架构候选的设计推敲统一交给规格入口；实现稳定后的简化与条件式经验捕获放在最终审查之前；所有关闭动作仍由完整交付证据控制。

第三轮采用：缺失技能与子 Agent 故障列为后续能力预检；前期没有目标时只保存草稿。用户随后明确授权新建 skills 源码仓库并发布 Issue，故恢复发布到用户指定仓库；继续保留 CC Switch 管理、原链接和已安装版本，不把草案直接部署。

### 参考与执行证据位置

- 本机规格入口：`C:/Users/will/.pi/agent/skills/to-spec-to-done/SKILL.md`。
- 本机规格生成入口：`C:/Users/will/.pi/agent/skills/to-spec/SKILL.md`。
- 本机实施入口：`C:/Users/will/.pi/agent/skills/implement-spec-to-done/SKILL.md`。
- 本机实现与审查先例：`C:/Users/will/.pi/agent/skills/implement-spec/SKILL.md`、`C:/Users/will/.pi/agent/skills/code-review/SKILL.md`。
- 本机架构调查与推敲先例：`C:/Users/will/.pi/agent/skills/improve-codebase-architecture/SKILL.md`、`C:/Users/will/.pi/agent/skills/grilling/SKILL.md`。
- [Superpowers verification-before-completion（固定源码）](https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/skills/verification-before-completion/SKILL.md)。
- [Compound ce-simplify-code（固定源码）](https://github.com/EveryInc/compound-engineering-plugin/blob/a3d6a8ee41a404b74391ee278423b52890b8920d/skills/ce-simplify-code/SKILL.md)。
- [Compound ce-compound（固定源码）](https://github.com/EveryInc/compound-engineering-plugin/blob/a3d6a8ee41a404b74391ee278423b52890b8920d/skills/ce-compound/SKILL.md)。

上述路径与固定版本是本次核查证据及执行入口，不是要求未来实现固定在某个应用文件的实现决定。依赖升级需重新检查兼容与本地适配，不能把不断变化的 main 当成已固定版本。

### 发布与后续交接

向已指定的 `williamxhero/skills` 发布：核对仓库身份、Issues 能力与已有相关 Issue，复用合格规格或发布本单 SPEC，应用 `ready-for-agent` 标签，回读正文及完整交付段落；若后来确有独立验收边界需要拆子 SPEC，再按原生关系要求组织，不把实现 tickets 预先建出。

发布完成后本入口结束。只有用户后续要求实施，或另一个已授权外层入口明确继续时，才调用 implement-spec-to-done。本次调用不启动后续开发。

## 执行与交付

> 使用 `/to-tickets` 将本 SPEC 拆为具有明确依赖关系的子 tickets，再使用 [$implement-spec](C:/Users/will/.cc-switch/skills/implement-spec/SKILL.md) 完成本 SPEC 及全部子 tickets。实现、测试和代码审查全部通过后，将任务分支的成果汇入 SPEC 集成分支，再合并到仓库主干。逐一确认待清理分支的成果均已纳入主干，且对应 worktree 无未保存改动后，移除本次创建的 worktree 并删除对应已合并分支。最后 push 主干，核验本地与远端一致。以“验收通过、主干已合并、临时资源已清理、远端已同步”为完成条件。任一步失败则停止后续步骤，保留未交付成果并报告阻塞；不强制删除或强推。
