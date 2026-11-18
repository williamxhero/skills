# Skills

个人 To-Done 工作流的源码归档与开发仓库。**CC Switch 继续负责本机技能的管理和分发；本仓库不是第二套安装器。**

## 管理边界

- 本机管理源保持在 `~/.cc-switch/skills/`。
- Pi 中现有链接继续指向 CC Switch 管理的目录，本次不改变链接、配置或安装状态。
- 本仓库保存源码版本和 GitHub Issues；不能把一次 Git pull 当成已经安装或部署技能。
- 后续经过审查的版本仍通过 CC Switch 导入／更新，再核验各客户端的实际状态。
- 依赖技能由 CC Switch 另行管理；本仓库不打包整个技能库，也不自动安装 Superpowers 或 Compound 的全局引导。

## 当前源码状态（未部署）

公开源码仓库：**[williamxhero/skills](https://github.com/williamxhero/skills)**。SPEC #1 分支已有三入口实现候选与静态检查；独立源码审查及前次窄证据文档审查／集成已完成，不等于最终行为验收或主干／远端交付完成。

- `to-spec-to-done/`：A1–A7 调查、每次强制自动推荐 grilling、原版 to-spec 发布／回读后停止；随目录交付唯一公共规则。
- `implement-spec-to-done/`：B1–B12 唯一当前 SPEC、隔离 TDD／集成、稳定后简化／条件经验捕获、独立审查与完整交付门禁；包含固定专项资源及本地适配。
- `improve-codebase-architecture-to-done/`：C1–C7 有范围证据调查、单候选或 no-change／blocked、复用前两入口；此前未安装，本次仍未部署。

前两个目录的**初次导出**曾是原样历史版本；当前源码已增强，安装中的旧版未因源码提交而改变。三入口必须搭配兼容的兄弟公共资源及另行管理的 Matt 依赖；复制单个目录不自动带上兄弟依赖。版本、完整资产／第三方通知、支持的 CC Switch UI 导入／更新及备份回滚事实见 [源码交接](docs/source-management.md)。根许可证尚待用户选择。

经授权且完成 CC Switch 导入／预检后，使用原生入口：

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

静态检查：`python -m unittest discover -s tests -p 'test_*_static.py' -v`，只证明包装／文档结构。真实 Pi、模拟 tracker、真实平台和未运行项分别记录在 [验收矩阵](docs/acceptance.md)。用户已同意在现有 Public 仓库使用明确标记的四个 TEST ONLY Issues，取代新建私有 sandbox；[目标调整记录](https://github.com/williamxhero/skills/issues/1#issuecomment-6952539322)不改历史发布快照。候选 `10046931` 的真实单／多 SPEC 发布、原生关系回读和无写入重复复用已观察；另有真实正文修复加明确注入响应丢失的源头恢复，不冒称平台拒绝或网络写失败。测试 Issues 均 OPEN／未实施，不是新开发任务；只有 SPEC #1 是当前开发规格。本次窄文档刷新待外层复核／集成。

**整体仍 NOT DONE／B9**：原生关系失败／未知关系写入、遗留实施 tickets 复用、完整候选实施／TDD／集成／审查／交付及无关 WIP 保护、有收益架构与关闭前收益、连续 live 新事实及其他条件路径仍缺；CC Switch 备份／安装／更新／发现／同名冲突／reload／回滚另需授权与实证。没有主干合并、push、清理、远端一致或关闭声明。

本仓库未提供自动同步、自动安装、工作流执行引擎或开机部署脚本。
