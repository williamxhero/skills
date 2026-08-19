---
name: iterate-quant-strategy
description: Iteratively optimize a quantitative trading strategy under the fixed AI quant research protocol, with frozen evidence gates, at least 100 closed research iterations, a required materially improved node that passes both the frozen development gate and historical challenge gate, project-local research records, and curated promotion of only reusable cross-project knowledge into the canonical research documents. Use for requests such as “迭代优化策略”, “至少迭代100次”, “持续优化直到材料性改善”, “通过开发门和历史挑战门”, or “把可复用研究经验回写到量化研究方法/协议/经验库”.
---

# AI 量化策略迭代

把策略优化作为可审计的研究循环执行。不得把“至少 100 次”解释为 100 次随意调参，也不得为了得到成功节点降低门槛、重复使用挑战集或改写失败事实。

## 固定知识源

将以下目录视为三份文档的唯一 canonical 仓库：

`D:\WILL\STOCK\apex_proj\strategies\research_method`

在开始研究前完整阅读：

1. `ai_quant_research_protocol.md`
2. `ai_quant_research_method.md`
3. `ai_quant_research_experience_library.md`

不要依赖摘要或旧会话记忆代替原文。若任一文件缺失、Git 仓库异常或三份文件互相冲突，停止并报告阻塞。以冻结的 `research_policy.json` 和三份文档中的更严格约束执行；不得静默放宽门禁。

## 区分项目事实与跨项目知识

`D:\WILL\STOCK\apex_proj\strategies\research_method` 是跨项目共享的方法仓库，不是任何单一策略的研究日志。严格分层：

- **项目层**：某个策略的全部运行事实和详细研究过程只写入该策略自己的 `strategies/<strategy-family>/` 工作区。
- **共享层**：三份 canonical 文档只保存可跨策略、跨项目复用的协议不变量、通用研究方法和抽象经验。

以下内容属于项目层，禁止写入三份共享文档：

- `G*` 代际范围、`C*` cycle、`S*` 节点、`H*` 假设、`E*` 实验、`D*` 决策或 `RT*` 攻击编号；
- 每批 checkpoint 的逐轮摘要、HTML checkpoint 注释、候选排行榜或流水账；
- 项目特有因子名、参数名、策略组件组合、具体 run 路径和当前 Pareto 前沿；
- rank IC、BH q、PF、回撤、收益、稳定分段数等项目具体数值；
- “本轮 G92-G96 做了什么、为什么没有打开挑战”一类项目裁决。

将这些内容保存到对应策略工作区的既有结构中，例如 `research/ledger/iterations/`、`research/ledger/decisions/`、`research/ledger/red_team/`、`research/decisions/`、`research/failures/`、`research/robustness/` 或项目已有的 checkpoint 文档。不要为了共享回写而复制或搬走项目证据。

共享文档允许写入的只有去项目化结论。例如把多个项目证据抽象为“开发门通过只授予历史挑战资格，不授予晋级资格”，并写清证据等级、适用范围、不可泛化边界和重开条件。共享条目不得包含足以还原某个项目检查点的编号、指标串或候选明细。

## 用独立 worktree 修改三份共享文档

不得直接修改 canonical 工作树。只有出现通过下述共享知识晋级门的候选时，才为共享文档创建唯一分支和独立 worktree：

```powershell
& <skill-dir>\scripts\start_docs_worktree.ps1 -ThreadId <当前任务唯一标识>
```

读取脚本输出的 `worktree_path`。只在该 worktree 中修改三份文档。不要在 canonical 目录切换分支，不要复用其他任务的 worktree，不要 force push、`reset --hard` 或覆盖他人的更改。

每个经验反馈检查点修改完成后，必须立即调用自动提交脚本，不得依赖人工 `git add`/`git commit`：

```powershell
& <skill-dir>\scripts\commit_docs_worktree.ps1 `
  -WorktreePath <worktree_path> `
  -CommitMessage "docs: record <策略族> <代际范围> research feedback"
```

脚本只允许提交三份 canonical 文档，并自动运行共享差异门禁；发现项目编号、checkpoint 流水、其他修改、缺失文档或错误分支时必须停止。没有实际差异时返回 `committed=false`，不得制造空提交。

完成研究和文档提交后合并：

```powershell
& <skill-dir>\scripts\merge_docs_worktree.ps1 -WorktreePath <worktree_path> -Cleanup
```

合并脚本会在获取合并锁前再次自动提交尚未提交的三份文档，作为最终安全网；正常流程仍应在每个反馈检查点调用 `commit_docs_worktree.ps1`，以保留 G5、G10、G15、G20 等独立提交历史。

若合并脚本报告并发冲突：

1. 确认 canonical 工作树已由脚本恢复为干净状态。
2. 在本任务 worktree 中 rebase 到最新 `master`。
3. 语义合并三份文档，保留双方不重复的证据、编号和约束；不得用 ours/theirs 整文件覆盖。
4. 完整重读冲突文件，检查经验编号、门禁和阈值没有丢失或被放宽。
5. 提交冲突解决并重新运行合并脚本。

## 冻结研究合同

在任何新绩效结果可见前：

1. 冻结主输入、策略族、数据身份、四段数据角色、PIT 规则、成本、成交口径、基线、父节点和 `research_policy.json`。
2. 在 ResearchPolicy 中明确开发门、历史挑战门和材料性改善阈值。材料性必须是相对冻结父节点或基线的经济幅度，不能事后定义。
3. 将 train、selection OOS、historical challenge/Red Team challenge 和 sealed final 分开；历史挑战不等于 sealed final。
4. 完成第零代基线复算、账户守恒、成本桥、归因守恒和确定性重放。第零代不计入 100 次。
5. 先探测数据、Adapter、ApexTrade CLI 和所需接口。接口、字段、PIT、数据覆盖或执行链路异常时立即停止并告诉用户，不得换近似字段、估计数据或其他来源继续。

## 执行至少 100 次有效迭代

把“一轮”定义为一个完整关闭的 Generation，而不是一次 backtest、一个参数点或一次 Red Team attack。一个 Generation 只有同时满足以下条件才计数：

- 注册至少一个新的、非重复、可证伪 Hypothesis；
- 建立不可变 StrategyNode 和 ExperimentSpec；
- 完成相应实验，或以结构化失败、数据阻塞、硬门拒绝等终态关闭；
- 生成 GenerationRecord，并为所有关闭节点生成 DecisionRecord；
- 更新完整尝试计数、失败模式、下一代队列和经验反馈记录。

只包含重复候选、改名候选或空队列的 Generation 不计数。参数近邻属于同一 Hypothesis 的平台测试，不得拆成多轮凑数。Red Team 攻击是候选验证，不单独计作新一轮。

每轮按三份文档执行固定循环：

1. 先查跨策略经验库和本策略 FailurePattern，命中旧失败且未满足重开条件时记为 `skipped_duplicate`。
2. 从当前 Pareto 前沿扩展结构假设，保持 local、novelty、contrarian 预算及机制多样性；先结构后参数。
3. 冻结 Hypothesis、节点、实验、数据、策略包和哈希，再 smoke、开发期、硬门、Pareto、历史挑战/Red Team。
4. 只从 ApexTrade 结构化产物裁决；保留成功、拒绝、跳过、超时、无成交、数据阻塞和执行失败。
5. Red Team 只攻击当前节点，不当场修补；修复必须成为下一轮的新 Hypothesis。
6. 关闭本轮并执行经验反馈步骤。

在第 100 次前不得因为提前找到漂亮节点而停止。第 100 次后若仍没有合格节点，继续运行新的高信息增益 Generation；不得围绕门槛做事后微调。只有数据、接口、计算预算或可证伪假设真正耗尽时才可标记 blocked。

## 要求材料性晋级节点

完成状态必须至少包含一个同一 StrategyNode，且结构化记录同时证明：

- `frozen_development_gate = passed`：通过预注册的开发/selection OOS 硬门和相对父节点门；
- `historical_challenge_gate = passed`：在冻结且此前未用于该节点选择的历史挑战角色中通过预注册门槛和致命 Red Team 条件；
- `evidence_level = promoted_material`：达到结果可见前冻结的材料性经济改善，而不只是 `verified_pareto_frontier`。

必须引用 StrategyNode、ExperimentResult、GenerationRecord、DecisionRecord、RedTeamRecord 和 ApexTrade 证据路径。三个条件必须属于同一节点，不能拼接不同候选的优点。不得把 historical challenge 误标为 `sealed_final_confirmed`；只有真正未触碰的 prospective final 一次性揭示后才能授予最终确认。

若客观上找不到该节点，不得捏造结果、降低阈值、删除失败或宣称完成。保留至少 100 次完整账本，报告 blocked 状态、已穷尽方向和重新开启条件。

## 项目反馈与共享知识晋级

每轮结束在**策略自己的工作区**生成结构化 `DocumentationFeedbackRecord`，至少包含 cycle、generation、证据路径、观察、适用范围、不可泛化、重开条件，以及三份共享文档各自的处理决定。该记录本身是项目事实，不得放入共享 `research_method` 仓库。

在 G5、G10、G15、G20、之后每 5 轮，以及材料性节点出现时，执行共享知识晋级评审。评审必须检查三份文档，但不是强制修改三份文件。只有候选满足以下条件才创建共享 docs worktree 并写入：

1. 结论可以脱离当前策略名称、代际、cycle、指标数值和候选明细独立成立；
2. 结论能改变其他项目的研究动作、门禁、查重或停止判断；
3. 有结构化证据支持，且已写出适用范围、不可泛化和重开条件；
4. 不是当前项目的状态汇报、失败清单、前沿快照或下一轮计划；
5. 删除所有项目标识后仍然准确，不会夸大证据等级。

不满足时，在项目 `DocumentationFeedbackRecord` 中记为 `project_only` 或 `not_promoted`，不得向共享文档写“待复核项目记录”占位。共享层宁可无修改，也不要积累项目流水。

通过晋级门后，按职责选择性更新：

- `ai_quant_research_experience_library.md`：写已被结构化证据支持、可跨策略迁移的经验；保持唯一经验编号、证据等级、适用范围、不可泛化和重开条件。
- `ai_quant_research_method.md`：写可复用的研究动作、调度、诊断、查重、信用分配或停止方法；区分策略个例和一般方法。
- `ai_quant_research_protocol.md`：只写被多轮证据支持的协议不变量、记录合同、门禁澄清或并发/审计约束；不得用单次收益结果修改阈值或削弱冻结规则。

一条知识只写入与其职责相符的文件；不要为了“反馈到三个文件”复制同义内容。若三份文件都没有符合晋级标准的更新，则不创建共享 commit。每次共享修改后逐行检查新增内容，确认没有项目编号、具体指标串、checkpoint 注释、项目路径、当前前沿或逐轮叙事，再调用自动提交脚本。

## 完成前检查

只有全部满足才交付完成：

- 有至少 100 个有效、完整关闭且可审计的 Generation；
- 同一节点通过冻结开发门、历史挑战门并达到 `promoted_material`；
- 全部尝试、失败、跳过、DecisionRecord、GenerationRecord 和 Red Team 证据保留；
- 所有项目明细已留在策略自己的工作区；规定检查点已评审三份共享文档，只有晋级的跨项目知识被写入；
- 三份共享文档不含本项目的代际/cycle/节点编号、具体指标、checkpoint 流水或当前前沿；
- docs worktree commit 已安全合并到 `master`，canonical 工作树干净；
- 最终 diff 不含无关修改，门槛未被事后放宽，sealed final 未被重复使用。

最终向用户报告：有效轮数、晋级节点 ID、三项门禁证据、材料性指标、文档反馈摘要、合并 commit，以及仍存在的风险或未完成的 sealed final。
