# Prerequisite delivery — selection, action readiness and return

本资源是实施入口必读规则，不是独立 executor。按 B1／B12 核对闭包并选择或返回；B2–B11 仍由入口执行，预检和实际失败均按下文 Action readiness 处理。项目权威词汇沿用 GLOSSARY.md（若存在）。

## 先读完整来源，再决定是否可写

保留用户原始提供的 **final delivery target**（仓库＋稳定 Issue 身份），整个请求不可被中间工作替换；另记恰好一个 **CURRENT SPEC**。执行记录只存这两个身份、阶段、版本和证据指针；tracker 的原生关系／项目既有本地约定是唯一任务图，不保存可执行下一节点计划。

在选择 CURRENT 或任何新交付写入（含拆票、产品、分支／集成、交付记录）之前，源头读取完整有限相关闭包：所有传递 incoming prerequisites 的全部分支、总 SPEC 的全部必需子 SPEC，以及 ticket 的已核验 owning SPEC 所需前置。每个节点均读完整正文、全部评论、实际类型／角色、契约、授权、父子／incoming／inverse 关系、状态和完整已有交付证据。分页读全；以仓库＋稳定 ID 去重，每个来源快照的共享节点只检查一次，保留来源。不可读／不可得／不完整关系是 unknown，不是空列表；无法完成闭包核验即停止新交付写入。

结合全部来源分类，不以标题、label、平台 type 或 sub-issue 形状单独判定：

- **单 SPEC**：满足自身需求与 incoming 前置，不制造总索引。
- **总 SPEC**：必需子 SPEC 加 incoming 前置均属验收义务；其 implementation tickets 不是额外子 SPEC。保留总体验收及独立集成要求。
- **提供的是 ticket**：核验唯一 owning SPEC 及反向 containment，在该 SPEC 执行工作流；保留原 ticket 的 final target 身份和原始验收，不把它伪造成 SPEC。跨 SPEC ticket blocker 只通过已核验、已授权的 owning SPEC 及其必要前置解析。无 owner／多 owner／矛盾来源均 blocked；不重挂、改标签或扫 siblings 来猜归属。
- **同 SPEC ticket edges**：留在 B5 原任务图，按已集成且验证的工作推进 frontier，不要求内部前票提前 CLOSED；原来的外层统一关闭门禁不变。

父节点、outgoing dependents 和无关 siblings 不自动成为工作；inverse blocking 只用于核验方向。核验 owning SPEC 不等于授权执行任意父／兄弟图。逐个核对解析后范围的授权，包括跨仓库；不凭本地 fixture 权限声称真实 GitHub／跨仓库执行许可，也不另设无来源的同仓库限制。

## Action readiness

1. **无法界定安全范围：** 核验闭包全部分支的 cycle、self-loop、containment 和 owner；目标、owner、依赖方向、来源或授权边界不明／冲突，导致不能核验闭包或证明安全范围时，停止所有新交付写入，先做获准的只读调查。记录具体节点／边和最小纠正条件；不猜 owner、不删边、取消或改状态制造 readiness。
2. **影响已知：** 对预检发现的缺项或实际失败，指出它阻止的动作与依赖者。已知且可界定的后续审批、人工／外部动作、输入、能力或时间门禁只暂停这些动作；显式 TEST ONLY 或人工专属仍不可代办。每个可继续动作须由 SPEC、已核验依赖或当前必需阶段证明为必要，且既有授权覆盖对象、仓库、环境、动作和风险；用来源证明它不依赖本缺项且符合既定顺序。必要性、自动推荐和 label 不授予新权限。新增授权仅在确需它的动作上等待，不把修复已获准动作等同于请求新权限。
3. **执行，再重算：** 保留原失败；直接在授权内调查原因、取得并校验权威输入、修复或采用契约允许的等价路径（独立角色见 [Pi 执行适配](pi-execution.md)）。实际执行不受影响且就绪的必要动作，留下候选绑定的结果证据；每次结果或新事实改变前提后重算下一步。未满足的必需步骤仍阻止依赖它的阶段，不调序、不降验收、不把建议／探针算交付；有 CURRENT 时仅推进它的可执行动作。不盲重试；外部写入未知先按下文回读。
4. **交还：** 只有没有获准、安全、符合既定顺序且能推进的下一步时，才报告 blocked。说明原目标、CURRENT（如已有）、具体受阻动作、已尝试路径和结果、保留的候选／证据、需要谁补的最小事实／能力／授权／时间条件，以及满足后第一步。不要求用户重批已有授权内的工作。

## 证据、选择与完整交付

先核验声称已交付的节点：适用验收实证、Git 主干／合并后验证／owned 清理／push 与实际 remote equality、完整 tracker 完成记录及 completed CLOSED 源头回读必须共同成立。状态／勾选本身不足；cancelled／not-planned 或缺证明的 CLOSED 不能解锁 dependents；按 Action readiness 核对可用的授权恢复路径，不能伪称 delivered。

无 CURRENT 时，完成闭包核验后，从尚未交付、所需前置均已真正交付且获准就绪的 SPEC 中选 **一个**：尊重显式业务顺序及依赖；只有其余条件等价且无既定顺序时，用 canonical identity 元组（规范仓库身份、稳定 ID 的字符串表示）按字典序升序 tie-break，并说明这不是业务优先级。向所需深度解析；已知门禁不排除有来源证明独立且就绪的分支。后续门禁不等于当前无法进入，但不选前置未交付或无获准就绪动作的 SPEC。跨 SPEC 串行；同一 SPEC 内仍按原 ticket 图执行。

每个实际进入的未交付 CURRENT 都完整执行[实施入口](../SKILL.md)的 B2–B11。后续 SPEC 的新 tickets／产品工作等待其 **所有** 必需前置完整交付并 completed CLOSED 回读；已有后续 tickets 保持未启动。有 CURRENT 后，即使阶段失败、另一分支就绪或新事实使行动暂停，也保留该 CURRENT 和原 target；按 Action readiness 处理，不切换 SPEC、不提前拆下游票、不称 Done。

已核验的 delivered prerequisite 按原身份及证据复用：不 reopen、不重写完成记录、不重复 tickets／TDD／产品编辑。若原 target 及其完整义务均确已交付，源头核验且无新事实使交付失效，返回 no-op；不为重跑制造活动或改动历史。

## 每次返回／恢复重新读源头

每次 completed CLOSED 后、resume 时、任何新事实／新边／reopen 出现时，在下一次拆票／新写入前重新读取 **原 final target 与整个剩余相关闭包** 的完整状态、角色、关系、证据和授权，并核对现存 CURRENT、保留候选与适用证据。重跑分类和 Action readiness；复用重新核验仍有效的证据，不沿缓存拓扑计划继续。新增 incoming edge 或重开的前置使依赖动作失去就绪资格，直至该前置再次核验为完整交付并回读 completed CLOSED。若影响现存 CURRENT，也只处理该 CURRENT 内仍就绪的动作；不能切去执行该前置、绕过它拆下游票或继续产品工作。新选择只在无 CURRENT（含上一个已完整交付并回读关闭）时进行。

所有必要前置成立后才恢复原 target 的工作（ticket 使用已核验 owner，原 ticket 验收仍须成立）。总 SPEC 的子项 CLOSED 不能代替总体验收：若有独立集成工作，以总 SPEC 为唯一 CURRENT 走 B2–B11；否则核对完整总交付义务及记录后才按 B11 关闭／回读。外部写入结果未知时按共享恢复规则处理：先回读实际状态（如 Git 实际远端 ref；tracker 源头 GET、跨状态的完整正文身份／覆盖去重及相关图回读），再按结果决定是否补写或重试。仅操作已授权、明确归属的资源，不另开架构扫描或引入 scheduler。
