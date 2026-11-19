# Skills

个人 To-Done 工作流的源码归档与开发仓库。**CC Switch 继续负责本机技能的管理和分发；本仓库不是第二套安装器。**

## 管理边界

- 本机管理源保持在 `~/.cc-switch/skills/`。
- Pi 中现有链接继续指向 CC Switch 管理的目录；外层验收未改管理配置或客户端链接。用户已通过 CC Switch 更新三个入口，当前 payload 与已交付源码一致。
- 本仓库保存源码版本和 GitHub Issues；不能把一次 Git pull 当成已经安装或部署技能。
- 后续经过审查的版本仍通过 CC Switch 导入／更新，再核验各客户端的实际状态。
- 依赖技能由 CC Switch 另行管理；本仓库不打包整个技能库，也不自动安装 Superpowers 或 Compound 的全局引导。
- `what-now/` 与 `commit-n-push/` 是个人维护的两份技能源码，随本仓库版本化；它们不是三个 To-Done 入口，`commit-n-push` 另有一份仍由 CC Switch 管理。

## 当前源码与安装状态（未完整验收）

公开源码仓库：**[williamxhero/skills](https://github.com/williamxhero/skills)**。已审查的三入口候选及先前证据已合入并推送 main `9ef92ba34a6e447ac27c5832424dd208dd0ea42e`，当时本地／tracking／实际远端一致，七个旧任务分支／工作树经归属及祖先核验安全清理。本次五文档候选检查点基于该已交付源码，位于最终文档独立复核／交付门禁之前；既有主干交付不等于 SPEC #1 完整验收。

- `to-spec-to-done/`：A1–A7 调查、每次强制自动推荐 grilling、按判断应用 `codebase-design`，由原版 to-spec 发布／回读后停止。新 SPEC 指定实施包装入口主持后续获准交付；随目录交付唯一公共规则。
- `implement-spec-to-done/`：B1–B12 唯一当前 SPEC、隔离实施／集成，按变更选择证据（源码核对、文本推演、适用代码 TDD）；保留稳定后简化／条件经验捕获、独立审查与完整交付门禁。
- `improve-codebase-architecture-to-done/`：C1–C7 有范围证据调查，按需要交付文字或验证过的图，单候选或 no-change／blocked，复用前两入口。

上述 #13 源码约定的验证与交付边界见 [#13 / #14 检查点](docs/acceptance.md#spec-13--ticket-14--source-checkpoint)；下方安装观察绑定先前交付版本。

前两个目录的**初次导出**是原样历史版本，第三个是未安装草案；历史快照不改。当前源码已增强，用户另行使用 CC Switch 4.0.31 更新后，三份管理 payload 分别为 4／62／2 文件且与源码逐文件相同。默认真实 loader 的 reload 前后均发现三个唯一名称、无相关诊断；实际交互 `/reload` 成功且 `/quit` exit 0，三个默认发现命令已展开并读取必需资源。这是管理式发现／加载证据，不是三条完整工作流通过。三入口必须搭配兼容的兄弟公共资源及另行管理的 Matt 依赖；复制单个目录不自动带上兄弟依赖。版本、完整资产／第三方通知、支持的 CC Switch UI 导入／更新及备份回滚事实见 [源码交接](docs/source-management.md)。根许可证尚待用户选择。

在兼容的 CC Switch 安装及预检成立后，使用原生入口：

```text
/skill:to-spec-to-done <需求>
/skill:implement-spec-to-done <单个或总 SPEC>
/skill:improve-codebase-architecture-to-done <范围或痛点>
```

规格发布不授权实施；扫描不授权重构。原版 grilling／架构技能仍独立交互。原生 CLI 的外部隔离 staging 观察不是安装，不改变现有客户端技能。

## 跟踪与开发

- 整合 SPEC：[Issue #1：规格、实施与架构改进三条 To-Done 工作流](https://github.com/williamxhero/skills/issues/1)，实现及验收中的任务；真实开放／关闭状态须源头回读，未完成项见 [验收矩阵](docs/acceptance.md)。
- Issue tracker：`https://github.com/williamxhero/skills/issues`，详见 `docs/agents/issue-tracker.md`。
- SPEC 正文以 GitHub Issue 为准；`docs/specs/` 保留发布内容的版本化快照。
- `docs/source-snapshot.json` 记录初始导出的文件哈希与安装状态；它是历史快照，不是自动同步清单。
- `docs/source-management.md` 说明后续源码、CC Switch 和客户端的交接规则。

静态检查：`python -m unittest discover -s tests -p 'test_*_static.py' -v`，只证明包装／文档结构。真实 Pi、模拟 tracker、真实平台和未运行项分别记录在 [验收矩阵](docs/acceptance.md)。用户已同意在现有 Public 仓库使用明确标记的四个 TEST ONLY Issues，取代新建私有 sandbox；[目标调整记录](https://github.com/williamxhero/skills/issues/1#issuecomment-6952539322)不改历史发布快照。候选 `10046931` 的真实单／多 SPEC 发布、原生关系回读和无写入重复复用仍有效；`9ef92ba` 的安装字节未改变这些入口。新增原生关系观察区分：明确 INJECTED 403、无服务器 POST 后如实 blocked；一次真实成功 dependency POST 被注入响应丢失后，下一条 gh 源头 GET 确认且无二次写入。原图及四份完整测试正文／标签／OPEN 均恢复；不是实际服务器权限拒绝或网络写失败。正式 #1–5 与 TEST ONLY #6–9 在最新源头记录均 OPEN；测试规格惰性、不实施不关闭，只有 #1 是当前正式开发 SPEC。

**SPEC #1 在本候选检查点仍 NOT DONE：最终外层门禁待完成。** 连续同 PID／session 新事实修订、原版独立交互及有界 S07 审查修复已观察；失败与恢复分开记录：

- **遗留实施：原门禁 FAILED，随后同 fixture 恢复成立。** 原 Standards 漏读规范、双审查越轴及三项过早关闭仍是失败历史；`repair-03` 实际重开／回读同三项 ID，再以 fresh 并行单轴审查完整读取适用规范，重验、核对本地 bare 交付／WIP、清理新 owned 审查工作树，更正记录回读后依次关闭 tickets／SPEC 并回读 CLOSED。未改生产／证明测试，未制造新 TDD 或空提交；Windows 原始输出 CRLF 与已批准 text seam 的 LF 明确区分。
- **原有收益架构：收益有效，完整生命周期不通过。** 实际图示渲染、同链交接、15 项兼容测试、关闭前局部性实验和本地交付均有证据；但九次 faulted-copy RED→unchanged-production GREEN 是 characterization，不满足 C6/B5 严格 TDD。独立精确合同审计确认不能用别处 A15 的真实 TDD 豁免；实际 C7 已追加更正并重开原 SPEC／ticket，保持 OPEN／BLOCKED B5。独立的 UTC 跨日 prospective fixture 初次因 CRLF/LF 证据收集器错误在 semantic RED 前 BLOCKED，fresh merger 正确拒绝 no-op；后来明确授权的 **AP-R 同 fixture 恢复**完成真实公开 CLI RED→最小生产修正→GREEN、稳定简化、完整规范并行审查、关闭前收益／兼容／回退限制验收、本地 bare 交付与有序关闭／C7 STOP，已获独立观察核验。这是有界本地成功，不是原 AR 的修复或正式 #1 完成。

精确合同审计接受 A09/S10 的有界故障行为证据，但它仍是注入 403（无 POST）／真实 POST 成功加注入响应丢失，不是真实 GitHub 权限拒绝。旧备份可读且符合受支持恢复输入，未实际恢复；不另造无条件卸载／恢复或所有条件式技能分支必测。本检查点先于最终文档独立复核／新鲜验证／主干集成及合并后验证／owned 任务资源清理／push 与实际远端一致、正式完成记录完整回读及按 #2→#3→#4→#5→#1 关闭回读。最新完整只读源头检查 `parent-source-check-3b` 保留 #1–9 OPEN；较早 `-3` 的 GET 超时不是完整检查。在 #1 CLOSED 前不启动暂缓增强。详见[验收矩阵](docs/acceptance.md)；[最新进展评论](https://github.com/williamxhero/skills/issues/1#issuecomment-6969464343)不是完成记录。候选固定 SHA／tree 及后续实际交付以外部候选报告和正式 Issue #1 为准，不在本检查点预写自身提交号或完成结论。

本仓库未提供自动同步、自动安装、工作流执行引擎或开机部署脚本。
