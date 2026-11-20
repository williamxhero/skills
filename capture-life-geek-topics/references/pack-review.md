# 资料包精读与审计

仅在给定资料包目录或要离线复评时使用。G/S/P 和 Reach 继续按[循证精读规则](editorial-review.md)判定；数据包不改变准入、原排序和历史状态。

## 定向配额与缺口

UTF-8 的 `pack.json` 含 `pack_id`、`audience` 和非空 `modules`。每项有唯一 `module_id`、`reader_decision`、`priority`（P0/P1/P2）及三个字符串数组：

- `source_gap`：缺能支持动作、适用范围或停止条件的循证信源。`source_terms` 仅为开放的此类缺口填写英文预筛词，可为空。检索词在标题、URL、摘要/开头命中后，先按模块优先级、再按旧预筛排序分配各源配额；不扩展到新入口，不改变去重，不把命中当证据。
- `asset_gap`：本地标签图、国内产品的特定规格数据、示例和使用许可等。交给素材核验或制作，不进入采集优先排序；需要查品牌公开信息也属于本地素材核验，不是补采外文循证证据。

同一份目录用于 `capture --per-source 2 --pack <pack.json>` 与 `finalize <capture.json> <reviews.json> --pack <pack.json>`；采集输出记录了目录指纹时，结账必须匹配。单条候选不自动关闭任何缺口；确认补齐后才更新项目目录。冻结的旧采集件可以直接用新版 finalize 离线复评，不改历史、不 `remember`。

## 回读本地判断

按[本地适用判断](opportunity-review.md#本地适用判断)填写同级评审。`tool_candidates` 是兼容的工具清单，不等于优先推荐；读 `local_report` 看条件和原文边界，`priority_urls` 只列 `local_priority: true` 的记录，不取工具清单前几条代替。同档沿用工具分数及旧顺序。包内须有已核 `action_audience`，本地 `audience` 也要逐字匹配目录；有显式本地评审时，任一人群不符只取消本地推荐，不否定原循证行动。无本地评审的旧工具输入仍拒绝显式不匹配。没有本地评审显示未评，不重算旧分。本地两类缺口不是目录覆盖更新；仅目录中已明确开放的信源缺口及匹配 `source_terms` 能影响以后获授权的定向采集，不自动加词或抓取。

## 评一条行动，不评整篇文章

仅在循证已通过的审核意见中补 `tool_review`：

| 字段 | 写法 |
| --- | --- |
| `module_id` | 目录中的 ID；适合做工具但和本包无关填 `null`。 |
| `reader_decision` | 写这条素材真正支持的用户决策，不直接抄目录标题。 |
| `decision_rule` | 可执行的比较、分步或分支；`decision` 得分时必须填写，不强行编 If–Then。 |
| `deliverable_type` | `checklist` / `comparison_matrix` / `decision_tree` / `calculator` / `sop` / `record_sheet`；无适合形态填 `none`。 |
| `criteria` | 下列四个键都要写。每项无支持填 `null`（0）；有支持填 `{"basis":"article 或 checked_source","locator":"章节或 URL/PMID 加定位","reason":"为何满足"}`（1）。编辑设计的版式/节奏可记 `editor_design`，不能拿它单独当计分证据。 |

| 键 | 可得 1 分的观察 |
| --- | --- |
| `inputs` | 读者确实能取得执行必需的输入，不把试验条件当现成商品数据。 |
| `decision` | 给定输入，能依凭资料选择、比较或操作。 |
| `check` | 能核对做了什么或是否完成，不要求短期证明临床改善。 |
| `exceptions` | 碰到资料缺失、不适用或需停止时，有可操作的边界。 |

0–1 分是 `education_only`，2–3 分是 `module_candidate`，4 分是 `prototype_candidate`；后两类须有具体工具形态。全部只是编辑候选，不是成品、付费意愿或定价结论。缺整个 `tool_review` 是未评，不是 0 分。用燕麦标签区分“总纤维”和“β-葡聚糖”可做核对卡，标签不标就停，不由总纤维倒推；咖啡因试验的研究条件不能变成个人精确截止线计算器。

**交付完成：**对照 `pack_review.tool_candidates`（包内）、`out_of_pack`（包外）、`unreviewed_urls` 和 `gaps.source_gap`／`gaps.asset_gap` 报告候选及去向。即使目录某节没有候选，仍交付其两类未补项；按 `module_id` 回查，包外候选单列。此流程不启动新的采集入口、素材制作、发布或销售。
