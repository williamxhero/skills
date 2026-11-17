# CC Switch 管理与源码交接

## 当前来源，不等于安装

公开源码仓库：[williamxhero/skills](https://github.com/williamxhero/skills)。三个入口已在 SPEC #1 分支实现为可审查的源码候选；尚未据此更新 CC Switch 管理源或客户端。初次归档时前两个入口是原样导出，第三个是未安装草案；后续源码已经改变，不能再把当前目录称为原样导出。`source-snapshot.json` 保留初始文件哈希和安装状态，`specs/` 保留发布原文，两者均为历史输入，不随当前状态改写。

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
| `implement-spec-to-done` | Pi 执行适配、三个固定专项资源及完整所选目录 | `../to-spec-to-done/references/` 两文件；Matt `grilling`、`to-tickets`、`implement-spec`、`tdd`（tests／mocking）、`code-review`；条件式设计／冲突技能 |
| `improve-codebase-architecture-to-done` | 有范围候选与两入口交接指令 | 上述两个配套入口；Matt `improve-codebase-architecture`（HTML-REPORT）、`codebase-design`；触发参考 |

三入口必须交接为**同一已审查兼容版本组合**；不是三个互相独立的安装包。架构入口还读取实施入口的 `references/pi-execution.md`。公共规则只维护一份，不复制三份，也不放在不会随目录交付的 `_shared`。先安装／更新公共规则所在入口，再核对实施与架构入口；这只是授权部署的建议顺序，不是自动升级授权。中途版本不兼容停用受影响流程并恢复兼容组合。

以实际加载 SKILL.md 的父目录为锚点解析兄弟相对路径；只有调用方显式给出 skill root 才使用它。记录路径、提交或 hash，检查同名来源、所有必需正文及分支参考；缺失／不可读／来源不明就 blocked。单目录复制不能证明依赖齐备，符号链接也不能补回从未复制的资源。Matt 依赖另由 CC Switch 管理，仓库不复制其模板或整个技能库。

## 固定专项资源与适配

`implement-spec-to-done/references/upstream/provenance.json` 是逐文件原始来源、commit、SHA256、许可证哈希及改名声明的清单。每个专项先读本地 `adapter.md` 再执行 `instructions.md` 及触发参考。

| 来源 | 固定 commit | 包含与支持范围 |
| --- | --- | --- |
| obra/superpowers | `22f0b463e7a5e579904dd6211a520321035526f7` | verification-before-completion：1 原文件，完整验证门禁 |
| EveryInc/compound-engineering-plugin | `a3d6a8ee41a404b74391ee278423b52890b8920d` | ce-simplify-code：12 原文件；包装器只支持当前 SPEC pipeline |
| 同上 | 同上 | ce-compound：39 原文件；capture、non-interactive、Full，一次一个已验证错误认识 |

52 个上游文件字节不变，只将各自主文件 SKILL.md 改名为 instructions.md，避免新增公开入口；保留各目录 MIT LICENSE。适配单独标明实际 Pi 新进程工具映射、主流程阶段交接、证明测试保护、compound 权威术语替换与 historian 仅 scratch 写入差异。保留可选参考不代表激活 refresh、standalone、bootstrap 或第二条 ce-plan／ce-work。Bash／Python／Unix 命令在实际 capture 环境另做能力预检。

仓库自有内容的根许可证尚待用户决定；不因仓库 Public 自行选择许可证。随包第三方通知必须保留。

## 支持的导入、更新与回滚（尚未执行）

依据只读研究：CC Switch 源码 `a1216b7e359466be98f3c783cc290e7040de26d4`，不是对当前运行二进制版本的保证。研究记录为执行包 `research/findings.md`；关键实现位于 `src-tauri/src/services/skill.rs` 的 install／update_skill／install_from_zip／import_from_apps／copy_dir_recursive，UI 在 UnifiedSkillsPanel／useSkills。部署前重查实际版本。

1. **仓库路径**：CC Switch UI 仓库管理添加公开 GitHub owner/name/branch，发现并安装三个选定卡片到 Pi；已具仓库 metadata 的卡片使用 update／update-all。下载是 branch archive，不能声称任意 SHA 可直接安装。审查后固定发布分支内容，记录安装前后实际 hash／commit；branch 不是不可变 pin。已有同名本地卡片不能假定自动转成仓库来源，先核对冲突和支持的替换流程。
2. **ZIP 路径**：UI ZIP picker 支持包含技能根的发布包；每个选定根只复制自己的后代，兄弟依赖必须分别交付。ZIP 安装记录为 local 来源，无仓库 metadata；既有注册目录冲突会跳过，**重复 ZIP 导入不是原地更新**。需替换时，在明确授权、独立确认可恢复备份后，通过支持的卸载→ZIP 安装路径处理，不直接覆盖管理源。未经演示不保证私有 GitHub 下载认证；本仓库目前 Public；当前 GitHub 验收经用户同意改用本仓库明确标记的 TEST ONLY Issues，不再要求创建私有 sandbox，这也不是 CC Switch 下载／安装认证证明。
3. **导入现有客户端技能**：scan/import unmanaged app skills 扫描已知 app／统一目录，不是任意本地仓库路径导入 API。不得为触发扫描而擅自把文件放进客户端或替换链接。deeplink 添加仓库配置也不等于安装。Tauri IPC 命令不是一个已证明存在的独立 CLI。
4. **备份／回滚**：保存三个目录及 metadata／投影的可恢复基线和归属证据；update 的内置备份为 best effort，须独立确认备份存在且内容完整。应用提供 restore_skill_backup；恢复指定备份或经授权重装兼容旧组合后，重查管理源、客户端实际目标／内容、hash 与依赖，再 `/reload` 并实际执行入口。源码研究不是已通过的恢复演练。

授权部署验收须分别记录：所选源码／版本组合、CC Switch 实际版本与操作、备份／恢复点、三目录 hash、客户端投影和同名冲突、Pi `/reload` 后发现及命令展开、共享与条件资源可读、真实运行结果。现阶段这些管理式部署步骤仍 pending，不能用 `shutil.copytree` 或外部 staging 替代。

## 更新与证据责任

先比较 CC Switch 当前内容与所选源码；存在管理侧修改就审查合并，不盲目双向覆盖。更新上游时重核原始清单／许可证、适配及全部参考闭包，并刷新受影响模型／平台证据。

见 [验收矩阵](acceptance.md) 和 [测试边界](../tests/README.md)。矩阵只索引证据，不成为任务状态权威；Issue 是目标／验收权威，tickets／原生依赖是任务图。STATIC、模拟 tracker、真实 Pi／模型、真实 GitHub、独立审查、CC Switch 导入及远端同步分别报告。当前 `10046931` 候选已有真实 GitHub 单／多 SPEC、原生图回读及无写入重复复用；用户 `同意` 的[源头调整记录](https://github.com/williamxhero/skills/issues/1#issuecomment-6952539322)将测试目标改为本仓库四个明确标记的 owned TEST ONLY Issues，历史发布快照不改。另有真实正文编辑成功、明确注入响应丢失后的源头恢复，不是实际 GitHub 拒绝／网络写失败。正式 #1–5 正文／状态／标签／图和隔离 fixture 内容未变；测试 Issues OPEN／未实施，当前开发 SPEC 仍只有 #1。

整体仍 NOT DONE／B9：原生关系拒绝／未知关系写入、遗留实施票据复用、完整候选实施交付／WIP、有收益架构／关闭前收益及连续 live 新事实等验收仍缺；管理式备份／安装／更新／投影／同名冲突／发现／reload／回滚未授权、未实测。此前独立源码审查及窄证据文档复核／集成已完成，本次新 GitHub 证据文档待外层窄复核／集成。显式 staging 不是安装；没有主干合并、push、清理、远端一致或关闭证据，不宣称全部交付，不关闭 Issue。
