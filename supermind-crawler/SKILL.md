---
name: supermind-crawler
description: 安全执行 SuperMind 研究页、Jupyter iframe、在线 kernel 和数据导出归档任务。用于 crawler 已确定目标涉及 SuperMind，或用户直接要求通过 SuperMind 抓取分钟线、概念历史、停牌或北交所数据、批量导出 Parquet、恢复 SuperMind Notebook 长任务时。
---

# SuperMind 爬虫

这是 `$crawler` 的 SuperMind 专项约束层。通用爬取、批处理、完整性和归档规则由 `$crawler` 提供；本技能只增加 SuperMind 独有的入口、Notebook、项目归档和数据质量契约。

## 技能依赖

1. 若当前任务尚未使用 `$crawler`，先调用 `$crawler`，再继续本技能。
2. `$crawler` 已使用时，不要重复初始化浏览器连接；在同一个 Edge `Profile 1` 和 Codex Chrome Extension binding 上继续。
3. 完整读取并执行 [references/supermind.md](references/supermind.md)。该文件中的规则均为本技能必需约束，不是可选背景资料。

## SuperMind Jupyter 操作授权

当前用户已对本技能授予持续授权：Agent 可以在 SuperMind Jupyter 环境内执行完成当前任务所需的任何操作，无需为该环境内的单项操作再次请求确认。这包括创建、打开、保存和执行 Notebook/cell，上传、下载、重命名或删除本任务文件与目录，以及创建、interrupt、删除本任务拥有的 kernel 和 session。

该授权只覆盖当前任务范围内的 SuperMind Jupyter 资源；不得影响用户或其他任务拥有的 Notebook、文件、kernel 或 session，也不覆盖上层系统安全约束或 SuperMind 以技术方式实施的访问限制。

## 进入 SuperMind 前

1. 读取 `D:\WILL\STOCK\supermind_proj\AGENTS.md`、`config/supermind-archive-policy.json`、`docs/supermind-archive-policy.md`，以及本次 collector、测试和运行文档。
2. 在浏览器或 Notebook 动作前完成 `$crawler` 的全局身份硬门槛，并额外完成 SuperMind 研究页、iframe 和错误 URL 门槛。
3. 对新增或修改的 collector，先加载并强制校验归档策略；legacy collector 仍写 CSV 时，先迁移到压缩 Parquet 契约再执行。
4. 先 dry-run，再用一个交易日完成端到端 smoke run；只有 smoke run 的生成、下载、验证、远端清理和 kernel 清理全部通过，才扩大范围。

## `.ipynb` 生命周期硬门槛

1. 每次开始使用一个 `.ipynb` 时，记录 notebook 路径、页面/tab、Jupyter session ID、kernel ID，以及哪些资源由本任务创建或拥有。
2. 每个 `.ipynb` 使用结束后，无论成功、失败、取消或抛出异常，都必须在 `finally` 中完成以下动作：
   - 保存本任务必须保留的代码、输出和证据。
   - 停止执行，interrupt 并 delete 本任务拥有的 kernel，关闭本任务为该 notebook 创建或拥有的 Jupyter session。
   - 关闭 SuperMind/Jupyter 界面中的该 `.ipynb` 页面或 editor tab，并复查它已不再处于打开或运行状态。
   - 释放该 notebook 创建的大型 frame、临时变量和当前任务的精确临时 runtime 资源。
3. 当前 `.ipynb` 的页面、session 和本任务拥有的 kernel 未全部关闭清理前，不得打开或开始使用下一个 `.ipynb`。
4. 清理失败时做有界重试；仍失败则记录 notebook 路径、session/kernel ID 和失败步骤，停止任务并报告阻塞，不得把该 notebook 或整个任务标记为完成。
5. “关闭并清理”默认不等于删除 `.ipynb` 文件。只有该文件是本任务创建的明确临时文件且清理契约要求删除时，才删除精确目标；用户 notebook 和其他 session 一律保持不动。

## 失败与完成

- 登录、iframe、kernel、Contents API、数据完整性或归档策略任一门槛失败时 fail closed，不生成正式目录，不跳过日期，不把部分产物标为完成。
- 只清理当前任务创建的精确远端目录、本地 staging 和 kernel；不得影响其他 Jupyter session 或任务。
- 只有所有用过的 `.ipynb` 页面均已关闭、对应 session 和本任务 kernel 均已清零，并且本地正式归档、manifest/hash/Parquet 验证、远端精确清理和浏览器复查全部通过，才能报告完成。
