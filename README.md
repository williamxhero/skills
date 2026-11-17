# Skills

个人 To-Done 工作流的源码归档与开发仓库。**CC Switch 继续负责本机技能的管理和分发；本仓库不是第二套安装器。**

## 管理边界

- 本机管理源保持在 `~/.cc-switch/skills/`。
- Pi 中现有链接继续指向 CC Switch 管理的目录，本次不改变链接、配置或安装状态。
- 本仓库保存源码版本和 GitHub Issues；不能把一次 Git pull 当成已经安装或部署技能。
- 后续经过审查的版本仍通过 CC Switch 导入／更新，再核验各客户端的实际状态。
- 依赖技能由 CC Switch 另行管理；本仓库不打包整个技能库，也不自动安装 Superpowers 或 Compound 的全局引导。

## 当前源码状态

- `to-spec-to-done/`：从 CC Switch 原样导出的现有版本，包含 `agents/openai.yaml`。
- `implement-spec-to-done/`：从 CC Switch 原样导出的现有版本，包含 `agents/openai.yaml`。
- `improve-codebase-architecture-to-done/`：新增的未安装源码草案，不是已验证或已部署能力。

前两个目录保留导出时的完整内容，没有提前实施规格中的增强。三条流程的整合、强制自动推荐 grilling、专项依赖适配与行为验证由 GitHub SPEC 跟踪。

## 跟踪与开发

- 整合 SPEC：[Issue #1：规格、实施与架构改进三条 To-Done 工作流](https://github.com/williamxhero/skills/issues/1)，当前为待实施的开放任务。
- Issue tracker：`https://github.com/williamxhero/skills/issues`，详见 `docs/agents/issue-tracker.md`。
- SPEC 正文以 GitHub Issue 为准；`docs/specs/` 保留发布内容的版本化快照。
- `docs/source-snapshot.json` 记录初始导出的文件哈希与安装状态；它是历史快照，不是自动同步清单。
- `docs/source-management.md` 说明后续源码、CC Switch 和客户端的交接规则。

本仓库未提供自动同步、自动安装或开机部署脚本。
