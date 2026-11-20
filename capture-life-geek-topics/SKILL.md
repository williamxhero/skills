---
name: capture-life-geek-topics
description: 循证话题原文采集。用于筛选可执行的生活方案、按资料包缺口选材、核查核心人体研究，或用历史候选池复跑对账。
---

# 循证话题原文采集

产物是**逐链接审计、带依据的候选清单、可接续的历史池**。到此停止；采集授权不包含部署定时任务、制作媒体或发布。

从内容工作区（本机 `E:/WILL/自媒体`）运行 `python -B scripts/protocol_capture.py`。技能引用项目里的 CLI，不另带一份爬虫。若当前目录没有该文件，先定位工作区；找不到就报告缺少的依赖。下文命令用 `<cli>` 表示上述调用。JSON 均以 UTF-8 保存；在项目 `.local/` 为本轮建独立目录，历史池使用跨轮稳定路径。真实采集优先用 `yosef-server`；先读其连接说明，让执行主机能读取本轮所用的脚本和同一历史快照。

无目录时，结账前先读[题材比较](references/opportunity-review.md)，分别评题材和文章。有资料包目录时，先读[资料包精读与审计](references/pack-review.md)，在 `capture` 与 `finalize` 使用同一份 `--pack <pack.json>`；精读只给通过循证门槛的行动评工具。另交候选工具、待补信源、本地素材三项。若只复评冻结样本，直接用原采集快照和新评审文件 `finalize`；不重新抓取、不写入历史池。

两种模式都要区分原文行动与本地推荐。按[本地适用判断](references/opportunity-review.md#本地适用判断)给一个 URL 的一次回放指定一个场景；缺旧判断记未评，不改旧榜。Reach 的例子只帮助说明覆盖范围，题材受欢迎不证明具体行动适用。

1. **定范围。** 默认四个公开入口：Precision Nutrition 与 Stronger By Science 的 RSS、FoundMyFitness Topics、Huberman Newsletter。查新增的学习、烘焙、训练 RSS 时，先读[更新源选用与验证](references/recurring-sources.md)，显式传 `--source`。常态首轮用 `<cli> capture --per-source 2`，复跑追加 `--history <history.json>`；对于原四源，三者 `--per-source`、`--history`、`--pack` 均省略时才是固定试点。历史缺失时区分首次运行与丢失恢复；后者先找最近的已完成审计，不悄悄从空池开始。**完成：**源、配额、执行主机、本轮目录和历史来源明确。
2. **采正文，对入口。** 无目录时默认把上一步的 capture 分两段，同一源、配额和历史参数不变。先加 `--index-only`，保存原样的本地 `index.json`；只看 `items` 的短标题和摘要。逐条判断谁需要完成什么反复或关键决策，什么工具能帮忙；仅有热门词或工具名不算。另写 `signals.json`（按入口 URL 键控，每项必填 `source, excerpt, reader, task, decision, tool, rationale`；能辨认中国目标读者的具体任务时可加 `local_task: {audience, starting_scenario, decision}`，三项均为非空短文本（最多 500 字符），不知道则省略，不按来源国或语言排除；excerpt 必须是本条标题或摘要的短原话）。不足就略过，全无则 `{}`。再以 `--from-index index.json --signals signals.json` 取代 `--index-only`，不重抓入口。报告赢得名额的原话和编辑理由，明确它不是行动证据。只使用本轮自行保存的快照，不接受外来快照或改写指纹。 保存 `capture.json`，分别检查 `indexes` 和 `items`。RSS/Atom 必须有合法根节点，HTML 专题页须解析到文章链接；HTTP 200 本身不是成功。`entry_blocked` 保留状态码、错误和尝试记录；只在既有授权内回读同一公开入口，否则交付部分覆盖。连接重置或超时由 CLI 最多重试两次（1、2 秒），不要再套一层全源重试。**完成：**每个入口有结果，入口失败与正文失败分列；正文有来源和指纹。
3. **把精读位给可用问题。** 检查标题、RSS 摘要/正文开头或专题 URL 产生的预筛标签。普通无目录优先按已说明的任务线索分配名额，再看原预筛排序；固定试点不改名额；资料包模式把命中待补信源的条目优先送去精读。预筛只分配配额。保留预算外条目；区分历史已读、近期同题和本轮重复。人体循证轨的正文不足 800 英文词是篇幅规则，不是编辑判断；`practical_source_only` 单列实操素材，不送人体循证精读。**完成：**每个候选 URL 有去向；`pending_review` 是本轮待办，不是入选。
4. **逐篇核证。** 对每个待办读正文，先圈定一个目标、人群和结局，再选最能检验这个切口的 1–2 个 PMID。用 `<cli> trace --provider ncbi <PMID> [PMID]` 或默认的 Europe PMC 接口取公开摘要；同一 PMID 换接口不增加文献数，追到这一层即止。记录正文直引还是编辑补查；摘要不足就缩小结论或拒绝，不跨付费墙。按[精读判例与字段](references/editorial-review.md)为每个待办写一条 `reviews.json`。**完成：**每条入选意见都能从原文定位到行动、从论文定位到对应结局；每条拒绝都能指出被否决的切口。
5. **结账再记忆。** `<cli> finalize <capture.json> <reviews.json>` 成功后保存 `audit.json`。核对 `counts.discovered = skipped_by_rule + fetch_failed + review_rejected + passed`；有回捞时这是**处理池总数**，本轮入口发现与历史回捞按 `pool_composition` 分报。以单写者运行 `<cli> remember <audit.json> <history.json>`，回读状态计数。`quota_skipped` 可回捞；`rejected_after_read`、`passed` 按 URL 跳过；已入选的同主题近期暂缓（30 天），不同于永久拒绝。**完成：**交付审计路径、来源覆盖/缺口、计数与入选原文链接；保留按 Reach 再按 G/S/P 的循证榜。无目录时默认另交 `opportunity_ranking.markdown`：在已有 URL 评审中填 `opportunity_review`，写 `topic, audience, reader_task, artifact, contribution, actionable_protocol, locator, limit, tier`。从同一 URL 已通过循证门槛的精读记录逐字复制 `actionable_protocol` 和 `locator` 到嵌套的 `opportunity_review` 同名字段；`limit` 必须等于该记录的 `scope_limit`。精读时选作 `locator` 的短原话须来自本篇正文的同一行。推荐行动前，编辑独立对照 `study_condition`、`scope_limit` 和原文，把适用行动人群写入同版基础记录 `action_audience`；模块和主体的 `audience` 须逐字复制该值，仅背景或未评则不要求。编辑核对这段在原研究人群和条件下实际支持所述行动；不能由引文匹配推断支持。分档 `usable_module`、`background`，若确能支撑主体才用 `pack_backbone` 并说明 `reuse`。无法判断则不填，标未评，不承诺效果。没有通过也照实交付，不把命中率或 Reach 当成平台热度。
