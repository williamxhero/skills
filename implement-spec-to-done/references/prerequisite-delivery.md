# Prerequisite delivery — B1 / B12 selection contract

本资源是实施入口的必需选择／返回规则，不是独立 executor。首次 B1、每次 B12、恢复及新事实出现时完整读取并应用；B2–B11 仍由入口原流程执行。项目权威词汇沿用 GLOSSARY.md（若存在）；本资源不另立词表。

## 先读完整来源，再决定是否可写

保留用户原始提供的 **final delivery target**（仓库＋稳定 Issue 身份），整个请求不可被中间工作替换；另记恰好一个 **CURRENT SPEC**。执行记录只存这两个身份、阶段、版本和证据指针；tracker 的原生关系／项目既有本地约定是唯一任务图，不保存可执行下一节点计划。

在选择 CURRENT 或任何新交付写入（含拆票、产品、分支／集成、交付记录）之前，源头读取完整有限相关闭包：所有传递 incoming prerequisites 的全部分支、总 SPEC 的全部必需子 SPEC，以及 ticket 的已核验 owning SPEC 所需前置。每个节点均读完整正文、全部评论、实际类型／角色、契约、授权、父子／incoming／inverse 关系、状态和完整已有交付证据。分页读全；以仓库＋稳定 ID 去重，每个来源快照的共享节点只检查一次，保留来源。不可读／不可得／不完整关系是 unknown，不是空列表；无法完成闭包核验即停止新交付写入。

结合全部来源分类，不以标题、label、平台 type 或 sub-issue 形状单独判定：

- **单 SPEC**：满足自身需求与 incoming 前置，不制造总索引。
- **总 SPEC**：必需子 SPEC 加 incoming 前置均属验收义务；其 implementation tickets 不是额外子 SPEC。保留总体验收及独立集成要求。
- **提供的是 ticket**：核验唯一 owning SPEC 及反向 containment，在该 SPEC 执行工作流；保留原 ticket 的 final target 身份和原始验收，不把它伪造成 SPEC。跨 SPEC ticket blocker 只通过已核验、已授权的 owning SPEC 及其必要前置解析。无 owner／多 owner／矛盾来源均 blocked；不重挂、改标签或扫 siblings 来猜归属。
- **同 SPEC ticket edges**：留在 B5 原任务图，按已集成且验证的工作推进 frontier，不要求内部前票提前 CLOSED；原来的外层统一关闭门禁不变。

父节点、outgoing dependents 和无关 siblings 不自动成为工作；inverse blocking 只用于核验方向。核验 owning SPEC 不等于授权执行任意父／兄弟图。逐个核对解析后范围的授权，包括跨仓库；不凭本地 fixture 权限声称真实 GitHub／跨仓库执行许可，也不另设无来源的同仓库限制。

## 完整闭包安全门禁

任何新交付写入之前检查全部分支的 cycle、self-loop、containment 矛盾及 owner 冲突。报告具体节点／边路径和最小纠正条件，保留已知事实／成果；不删边、取消、关票或猜 owner 来制造 readiness。

完整闭包内任一必需 human／external action、unowned、TEST ONLY、未知／不可读来源、授权不确定或必需能力缺失，均阻止 **ALL 新交付写入**，即使另一独立分支可运行。区分缺事实、缺能力、缺权限。自动推荐／label 不授予权限。未交付但可实施且已授权的 prerequisite 本身不是此类安全阻塞：它应成为待交付工作。

## 证据、选择与完整交付

先核验声称已交付的节点：适用验收实证、Git 主干／合并后验证／owned 清理／push 与实际 remote equality、完整 tracker 完成记录及 completed CLOSED 源头回读必须共同成立。状态／勾选本身不足；cancelled／not-planned 或缺证明的 CLOSED 不能解锁 dependents。仅按适用授权恢复真实状态和补足证据，否则 blocked，不能伪称 delivered。

完整闭包通过安全核验后，从尚未交付且所需前置均已真正交付的 SPEC 中选 **一个** CURRENT：尊重显式业务顺序及依赖；只有其余条件等价且无既定顺序时，用 canonical identity 元组（规范仓库身份、稳定 ID 的字符串表示）按字典序升序 tie-break，并说明这不是业务优先级。向所需深度解析，授权未完成前置成为 CURRENT，不因它未完成就 blanket BLOCKED。跨 SPEC 串行；同一 SPEC 内仍按原 ticket 图执行。

每个实际进入的未交付 CURRENT 都完整执行[实施入口](../SKILL.md)的 B2–B11，不以选择／复用规则替代实施、验收或关闭门禁。

后续 SPEC 的新 tickets／产品工作等待其 **所有** 必需前置完整交付并 completed CLOSED 回读；已有后续 tickets 保持未启动。CURRENT 任一 mandatory 阶段失败就保留该 CURRENT、原 target、具体阶段／诊断／进展和最小继续条件并 STOP；不换独立分支掩盖失败，不称 Done。

已核验的 delivered prerequisite 按原身份及证据复用：不 reopen、不重写完成记录、不重复 tickets／TDD／产品编辑。若原 target 及其完整义务均确已交付，源头核验且无新事实使交付失效，返回 no-op；不为重跑制造活动或改动历史。

## 每次返回／恢复重新读源头

每次 completed CLOSED 后、resume 时、任何新事实／新边／reopen 出现时，在下一次拆票／新写入前重新读取 **原 final target 与整个剩余相关闭包** 的完整状态、角色、关系、证据和授权，重跑上述分类／安全核验并重新计算 CURRENT；重新核验共享已交付证据，不能沿缓存拓扑计划继续。新增 incoming edge 或重开的前置使下游选择失效，先核验新事实。

所有必要前置成立后才恢复原 target 的工作（ticket 使用已核验 owner，原 ticket 验收仍须成立）。总 SPEC 的子项 CLOSED 不能代替总体验收：若有独立集成工作，以总 SPEC 为唯一 CURRENT 走 B2–B11；否则核对完整总交付义务及记录后才按 B11 关闭／回读。外部写入结果未知时按共享恢复规则处理：决定是否重试前须完成源头 GET、all-state full-body 身份／覆盖去重及相关图回读。仅操作已授权、明确归属的资源，不另开架构扫描或引入 scheduler。
