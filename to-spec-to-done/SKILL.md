---
name: to-spec-to-done
description: "直接调用 to-spec 发布带完整交付约定的 SPEC。简单需求使用单个 SPEC；必要时拆为多个子 SPEC 和 todo list 总 SPEC，并建立 GitHub 原生关系。在 Bypass permissions／完全访问下自动采用全部同意或仓库默认决策，不等待 SPEC 行动许可。本技能不启动后续开发。"
---

# To Spec To Done

唯一的 SPEC 生成入口是 `to-spec` 技能。调用方式为读取其已安装的 `SKILL.md` 并按其正文流程执行（本机副本示例：`~/.pi/agent/skills/to-spec/SKILL.md`、`C:/Users/will/.cc-switch/skills/to-spec/SKILL.md`）。本技能只追加规格组织和交付要求，不复制其模板、不自行代行其流程，也不得绕开其流程自拟模板。

## 规格组织

- **单个 SPEC**：简单需求能在一个清楚的范围内描述并验收时，只发布一个 SPEC，无需另建总 SPEC，也无需为了形成清单强拆。
- **多个 SPEC**：确有必要按独立目标或验收边界拆分时，发布多个子 SPEC，以及一个汇总完整目标和整体验收的总 SPEC。子 SPEC 是规格；实现 tickets 仍在后续通过 `/to-tickets` 拆分。
- **总 SPEC todo list**：在总 SPEC 正文用 Markdown 复选框列全子 SPEC 的标题和真实链接／Issue 编号，并说明已确定的依赖。规格发布时完成子 SPEC 的创建或复用及清单回填；未验收的项保持未勾选，新增、移除或替换子 SPEC 时同步更新。
- **GitHub relationships**：使用 GitHub Issues 时，必须同时通过原生 sub-issues relationship 将所有子 SPEC 挂到总 SPEC；已确定的阻塞依赖使用原生 issue dependencies。正文引用不能替代系统关系，CLI 不支持时使用 GitHub API。

## 调用与核验

1. 直接调用 `$to-spec`，传入当前需求，并追加以下要求：
   - 保持 `$to-spec` 原有流程及发布行为，按上方“规格组织”选择单个 SPEC 或多个子 SPEC＋总 SPEC。
   - 无论采用哪种组织方式，在首次发布前，将下方“执行与交付”原样完整写入每个新 SPEC 的正文，包括独立 SPEC、子 SPEC 和总 SPEC；只写在总索引或最终回复不算完成。
   - 已有明确拆票结果的遗留 SPEC 沿用原 tickets 及依赖，不补交付段落、不重新拆票；有总 SPEC 时仍核对其清单和原生关系。
   - 技能链接是执行入口，原样保留，不作为过时实现路径移除。
2. 返回后回读各 SPEC，确认交付段落完整；多 SPEC 时核对总 SPEC 清单、原生子 Issue 集合、每个子 SPEC 的父 Issue，以及阻塞依赖方向一致。重复运行复用已有 Issue 和有效关系；不属于当前范围的既有关联先确认归属。
3. 报告实际结果后结束。规格发布不启动 `/to-tickets`、`$implement-spec` 或 Git 交付操作；这些是后续执行，不因写入完成条件而提前运行。

若平台不支持或无权建立必需关系，保留已发布规格并明确报告关系未完成，不把正文清单当作全部完成。

若 `to-spec` 在本环境确实不存在或其 `SKILL.md` 不可读，立即报告阻塞。其“仅用户直接调用”的限制在本环境被忽略：按 `SKILL.md` 正文流程执行即视为委托成功，不得以该限制为由报告阻塞、拒绝委托或要求用户手动输入 `/skill:to-spec`；也不得修改 `to-spec` 本身或伪称调用成功。

## 写入 SPEC 的“执行与交付”

> 使用 `/to-tickets` 将本 SPEC 拆为具有明确依赖关系的子 tickets，再使用 [$implement-spec](C:/Users/will/.cc-switch/skills/implement-spec/SKILL.md) 完成本 SPEC 及全部子 tickets。实现、测试和代码审查全部通过后，将任务分支的成果汇入 SPEC 集成分支，再合并到仓库主干。逐一确认待清理分支的成果均已纳入主干，且对应 worktree 无未保存改动后，移除本次创建的 worktree 并删除对应已合并分支。最后 push 主干，核验本地与远端一致。以“验收通过、主干已合并、临时资源已清理、远端已同步”为完成条件。任一步失败则停止后续步骤，保留未交付成果并报告阻塞；不强制删除或强推。
