# CC Switch 管理与源码交接

## 当前来源，不等于安装

公开源码仓库：[williamxhero/skills](https://github.com/williamxhero/skills)。三个入口的已审查候选已合入并推送 main `9ef92ba34a6e447ac27c5832424dd208dd0ea42e`；用户另行通过 CC Switch 4.0.31 更新后，管理 payload 已与该源码逐文件一致。外层验收没有改写管理源、DB、配置或客户端链接；源码提交本身不是安装。初次归档时前两个入口是原样导出，第三个是未安装草案；后续源码已经改变，不能再把当前目录称为原样导出。`source-snapshot.json` 保留初始文件哈希和安装状态，`specs/` 保留发布原文，两者均为历史输入，不随当前状态改写。

```text
公开仓库中的已审查版本
        ↓ 经授权的 CC Switch 导入／更新
CC Switch 管理源（默认 ~/.cc-switch/skills/<name>）
        ↓ CC Switch 原有投影（符号链接或复制）
各客户端技能目录（包含 Pi）
```

仓库不是第二套安装器。Git pull／push、外部隔离 staging、显式 `--skill` 加载都不是 CC Switch 部署。保留现有管理配置、DB、客户端链接和其他线程成果；不直接覆盖 SSOT，不将客户端重连到本仓库，不增加自动同步进程。实际管理根可能由 CC Switch 设置选择统一目录；以真实配置为准，不另造共享根。

## 三目录与依赖闭包

CC Switch 安装／更新**只递归复制所选技能目录**，不会解析 Markdown 链接来收集兄弟资源。发现 SKILL.md 后停止向该根内递归发现技能。仓库恰有三个公开 SKILL.md：

| 安装目录 | 随目录交付 | 另需兼容兄弟资源 |
| --- | --- | --- |
| `to-spec-to-done` | 唯一公共规则 `references/engineering-quality.md`、`auto-grilling.md` | 实际 Matt `grilling`、`to-spec`，按需设计技能 |
| `implement-spec-to-done` | Pi 执行适配、三个固定专项资源及完整所选目录 | `../to-spec-to-done/references/` 两文件；Matt `grilling`、`to-tickets`、`implement-spec`、`code-review`；条件式 `tdd`（tests／mocking）、设计／冲突技能 |
| `improve-codebase-architecture-to-done` | 有范围候选与两入口交接指令 | 上述两个配套入口；Matt `improve-codebase-architecture`、`codebase-design`；视觉分支触发 HTML-REPORT 等参考 |

三入口必须交接为**同一已审查兼容版本组合**；不是三个互相独立的安装包。架构入口还读取实施入口的 `references/pi-execution.md`。公共规则只维护一份，不复制三份，也不放在不会随目录交付的 `_shared`。先安装／更新公共规则所在入口，再核对实施与架构入口；这只是授权部署的建议顺序，不是自动升级授权。中途版本不兼容停用受影响流程并恢复兼容组合。

以实际加载 SKILL.md 的父目录为锚点解析兄弟相对路径；只有调用方显式给出 skill root 才使用它。记录路径、提交或 hash，检查同名来源、所有必需正文及分支参考；缺失／不可读／来源不明就 blocked。单目录复制不能证明依赖齐备，符号链接也不能补回从未复制的资源。Matt 依赖另由 CC Switch 管理，仓库不复制其模板或整个技能库。

## 固定专项资源与适配

`implement-spec-to-done/references/upstream/provenance.json` 是逐文件原始来源、commit、SHA256、许可证哈希及改名声明的清单。每个专项先读本地 `adapter.md` 再执行 `instructions.md` 及触发参考。

| 来源 | 固定 commit | 包含与支持范围 |
| --- | --- | --- |
| obra/superpowers | `22f0b463e7a5e579904dd6211a520321035526f7` | verification-before-completion：1 原文件，完整验证门禁 |
| EveryInc/compound-engineering-plugin | `a3d6a8ee41a404b74391ee278423b52890b8920d` | ce-simplify-code：12 原文件；包装器只支持当前 SPEC pipeline |
| 同上 | 同上 | ce-compound：39 原文件；capture、non-interactive、显式 Lightweight 或 Full，一次一个已验证错误认识 |

52 个上游文件字节不变，只将各自主文件 SKILL.md 改名为 instructions.md，避免新增公开入口；保留各目录 MIT LICENSE。适配单独标明实际 Pi 新进程工具映射、主流程阶段交接、证明测试保护、compound 权威术语替换与 historian 仅 scratch 写入差异。保留可选参考不代表激活 refresh、standalone、bootstrap 或第二条 ce-plan／ce-work。按实际 capture 分支预检所需 Bash／Python／Unix 命令及角色。#13 源码约定不改变下列历史版本的部署证据；见 [#13 / #14 检查点](acceptance.md#spec-13--ticket-14--source-checkpoint)。

仓库自有内容的根许可证尚待用户决定；不因仓库 Public 自行选择许可证。随包第三方通知必须保留。

## 支持的导入、更新与回滚（机制与实际观察分开）

依据只读研究：CC Switch 源码 `a1216b7e359466be98f3c783cc290e7040de26d4`，不是对当前运行二进制版本的保证。研究记录为执行包 `research/findings.md`；关键实现位于 `src-tauri/src/services/skill.rs` 的 install／update_skill／install_from_zip／import_from_apps／copy_dir_recursive，UI 在 UnifiedSkillsPanel／useSkills。部署前重查实际版本；本次实际运行版本 4.0.31 已单独观察，不将研究源码提交冒充二进制来源。

1. **仓库路径**：CC Switch UI 仓库管理添加公开 GitHub owner/name/branch，发现并安装三个选定卡片到 Pi；已具仓库 metadata 的卡片使用 update／update-all。下载是 branch archive，不能声称任意 SHA 可直接安装。审查后固定发布分支内容，记录安装前后实际 hash／commit；branch 不是不可变 pin。已有同名本地卡片不能假定自动转成仓库来源，先核对冲突和支持的替换流程。
2. **ZIP 路径**：UI ZIP picker 支持包含技能根的发布包；每个选定根只复制自己的后代，兄弟依赖必须分别交付。ZIP 安装记录为 local 来源，无仓库 metadata；既有注册目录冲突会跳过，**重复 ZIP 导入不是原地更新**。需替换时，在明确授权、独立确认可恢复备份后，通过支持的卸载→ZIP 安装路径处理，不直接覆盖管理源。未经演示不保证私有 GitHub 下载认证；本仓库目前 Public；当前 GitHub 验收经用户同意改用本仓库明确标记的 TEST ONLY Issues，不再要求创建私有 sandbox，这也不是 CC Switch 下载／安装认证证明。
3. **导入现有客户端技能**：scan/import unmanaged app skills 扫描已知 app／统一目录，不是任意本地仓库路径导入 API。不得为触发扫描而擅自把文件放进客户端或替换链接。deeplink 添加仓库配置也不等于安装。Tauri IPC 命令不是一个已证明存在的独立 CLI。
4. **备份／回滚**：保存三个目录及 metadata／投影的可恢复基线和归属证据；update 的内置备份为 best effort，须独立确认备份存在且内容完整。应用提供 restore_skill_backup；恢复指定备份或经授权重装兼容旧组合后，重查管理源、客户端实际目标／内容、hash 与依赖，再 `/reload` 并实际执行入口。源码研究不是已通过的恢复演练。

授权部署验收须分别记录：所选源码／版本组合、CC Switch 实际版本与操作、备份／恢复点、三目录 hash、客户端投影和同名冲突、Pi `/reload` 后发现及命令展开、共享与条件资源可读、真实运行结果。当前实际观察（外部证据包 `skills-1-20261117/continue-20261118/managed-report.md`、`facts.md`、`managed-verified.json`）：

- 用户执行 CC Switch 更新；只读 metadata 为 `williamxhero/skills/main`，三个完整 payload 的相对路径／SHA256 集合分别 4／62／2 文件，与 `9ef92ba` 相同；Pi 真实符号链接仍指向 CC Switch，不指向源码仓库。不重建 UI 点击时间线或推断操作作者。架构入口正文的“本次源码变更未部署／未安装”属于该版本形成时的旧检查点；D 的后续管理式发现／加载澄清当前状态，不据旧句否定实际安装，也不为本次证据刷新改入口或重新安装。
- Pi 1.5.2 的真实默认 loader 在 reload 前后均发现三个唯一名称、无这三个名称的相关诊断；实际交互 ConPTY `/reload` 显示成功状态，`/quit` 正常 exit 0。其他无关名称警告保留，未借机清理全局环境。
- 三个普通默认发现的 `/skill:` 命令分别真实展开并成功读取安装中的公共／阶段必需资源，终态 stop／nonaborted／exit 0。它们只获阶段资源读取授权，**不是完整工作流成功**；不能由显式 staging 代替此证据。
- 三份旧备份的 metadata 与两文件旧 payload 可读、hash 已保存，格式与受支持恢复输入相符；不是当前 4／62／2 文件候选的备份，未证明旧树完整性／兼容性，**没有实际卸载或恢复演练**。研究表明恢复需先不存在当前注册／目录，不能声称可原地覆盖；任何未来恢复仍需明确授权。依精确契约判断具体恢复要求，不把所有可选恢复路径新增为无条件必测。

## 更新与证据责任

先比较 CC Switch 当前内容与所选源码；存在管理侧修改就审查合并，不盲目双向覆盖。更新上游时重核原始清单／许可证、适配及全部参考闭包，并刷新受影响模型／平台证据。

见 [验收矩阵](acceptance.md) 和 [测试边界](../tests/README.md)。矩阵只索引证据，不成为任务状态权威；Issue 是目标／验收权威，tickets／原生依赖是任务图。STATIC、模拟 tracker、真实 Pi／模型、真实 GitHub、独立审查、CC Switch 导入及远端同步分别报告。`10046931` 的真实 GitHub 单／多 SPEC、原生图回读及无写入重复复用仍有效；用户 `同意` 的[源头调整记录](https://github.com/williamxhero/skills/issues/1#issuecomment-6952539322)将测试目标改为本仓库四个明确标记的 owned TEST ONLY Issues，历史发布快照不改。真实正文编辑加注入响应丢失之外，`9ef92ba` 新增原生关系有界观察：精确注入 403 不发服务器 POST、保留部分图并 blocked；一次真实成功 POST 加注入未知响应后，下一条 gh GET 回读并核对逆向，无二次写入。setup 一次 DELETE 与候选一次 POST 仅涉及 owned #8 blocked-by #7，最终原图及完整正文／标签／OPEN 恢复，#6 独立。不是实际 GitHub 服务器权限拒绝／网络写失败。正式 #1–5 正文／状态／标签／图未变；保留原评论，明确披露外层新增的进展评论，最新为[进展／更正](https://github.com/williamxhero/skills/issues/1#issuecomment-6969464343)，不声称所有评论未变或把进展当完成记录。最新完整只读 `platform-runtime/parent-source-check-3b/state.json` 精确核验正文／标签／图及原评论；较早 `parent-source-check-3` 在 #4 comments GET 超时而不完整。该检查点 #1–9 均 OPEN，测试规格惰性／未实施，当前正式开发 SPEC 仍只有 #1。

本五文档候选检查点仍 NOT DONE，最终外层门禁待完成。已交付 main `9ef92ba` 的实际远端一致与七个旧任务资源安全清理有独立记录；它早于本次证据检查点，不替代最终文档候选的独立复核和交付。真实 live 新事实、原版交互、S07 阶段审查修复见矩阵。遗留实施原 Standards 漏读／越轴／过早关闭门禁 FAILED，后来同 fixture `repair-03` 实际完成重开回读→fresh 合规并行审查→新鲜验证／owned 清理／本地 bare refs 一致→更正记录回读→tickets 再 SPEC 关闭回读；旧失败不追认通过，原 TDD／merger／简化／WIP／交付观察保留。

原 selected 架构有实际渲染、15 项兼容测试、关闭前局部性收益及本地交付，但 copied-fault characterization 不满足 C6/B5 严格 TDD；精确合同审计确认缺口，实际 C7 更正已重开原 SPEC／ticket 为 OPEN／BLOCKED B5，完整流程不是 PASS。独立的 UTC 跨日真实缺口＋同一策略 deepening prospective fixture 初次在 semantic RED 前被收集器 CRLF/LF 比较错误阻塞；后经明确授权的 AP-R 同 fixture 恢复已独立核验本地 B5–B11／C7：真实 TDD、关闭前收益／兼容／回退限制、本地 bare 交付和有序关闭成立。原失败历史保留，原 AR 仍 OPEN／BLOCKED B5；此结果不是新正式 SPEC／增强或管理部署。详细版本及证据集中见验收矩阵 AP／AP-R。合同审计未新增无条件恢复演练或所有条件式分支必测；已装载事实与源码旧检查点并存如上。尚待最终文档独立复核／新鲜验证／主干集成及合并后验证／owned 资源清理／正常推送与实际远端一致、正式完成记录完整回读与 #2→#3→#4→#5→#1 关闭及 CLOSED 回读；#1 CLOSED 前不启动暂缓增强。候选固定身份由外部 `final-doc-candidate-report.md` 记录，后续生命周期以正式 Issue #1 为准。本次刷新不改三入口／4／62／2 payload 或证明测试，不运行场景、不安装或恢复、不关闭 Issue。
