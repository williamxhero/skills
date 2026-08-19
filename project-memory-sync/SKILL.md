---
name: project-memory-sync
description: 使用 MemPalace 维护跨项目、跨 thread 共享的项目记忆。用于记录项目知识、同步上下文、整理 handoff、补充架构约定、更新共享记忆、查找历史决定，或在任务完成后沉淀已确认的稳定项目事实。所有项目共用同一个 palace，通过 wing 区分项目，通过 room 和关键词区分项目内记忆。
---

# Project Memory Sync

## 目标

把稳定项目事实写入共享 MemPalace，而不是写入仓库内 `/.codex/project-memory.md`。

- 同一个项目固定使用一个 `wing`
- 项目内记忆固定使用 `room`
- 条目内再补 `关键词`，便于后续精搜
- 只记录稳定事实，不记录临时分析、猜测和一次性调试过程

## 固定流程

按下面顺序执行，避免调用侧自己拼流程：

1. 预处理：确认项目 `wing`，先搜索是否已有相同记忆
2. 核心执行：把新的稳定事实写入共享 palace
3. 后处理：向用户说明这次新增或更新了哪些共享记忆

## 调用入口优先级

Codex 已全局配置 MemPalace MCP，优先使用 MCP 工具访问共享记忆。

优先级固定如下：

1. MCP 工具：当前会话中可见 `mempalace_*` 工具时，读写都优先用 MCP。
2. 本 skill 脚本：当前会话还没加载 MCP，或需要按 `--project-root` 自动维护项目到 `wing` 的映射时使用。
3. MemPalace CLI：只有在用户明确要求索引原始项目资料、聊天导出，或 MCP 与脚本都不可用时使用。

已配置的 Codex MCP server：

```powershell
codex mcp get mempalace
```

服务启动命令：

```powershell
python -m mempalace.mcp_server
```

如果当前 Codex 进程尚未刷新环境，也可以使用：

```powershell
py -3.13 -m mempalace.mcp_server
```

## 项目隔离规则

- 所有项目共用一个 MemPalace：默认是 `~/.mempalace/palace`
- 一个项目对应一个 `wing`
- 默认从当前仓库根目录名推导 `wing`
- 如果一条记忆对很多项目都适用，固定写入通用 `wing`：`coding`
- 如果用户明确指定项目名或 `wing`，以后继续复用同一个名字，不要来回改
- 需要跨项目搜索时，去掉 `wing` 过滤；普通项目内搜索默认带当前项目 `wing`

项目与 `wing` 的持久映射由脚本维护在：

- `~/.codex/mempalace-project-memory/projects.json`

## Room 约定

优先使用这些固定 `room`，不要随意发明同义词：

- `overview`：项目概览、目录职责、入口
- `decisions`：已确认技术决策
- `constraints`：硬约束、协议、边界条件
- `commands`：启动、测试、构建、验证命令
- `behavior`：已确认行为、缓存语义、接口约定
- `pitfalls`：稳定坑点、已知限制
- `handoff`：交接信息、下一步注意事项

如果确实不属于上面任何一种，再新增 `room`。新增后保持复用，不要制造多个近义类别。

## 何时写入

满足下面任一条件再写：

- 用户明确要求记录、同步、沉淀、补充项目记忆
- 任务执行后出现了已经被代码、配置、命令结果或用户确认过的稳定结论
- 新 thread 明显会反复用到这条事实

下面这些不要写：

- 临时排查路径
- 未证实猜测
- 一次性报错日志
- 只对当前一句回复有用的上下文

## 先搜再写

MCP 可用时，先确定当前项目 `wing`，再用 `mempalace_search` 搜索。`query` 只放关键词或短问题，背景说明放 `context`。

常用 MCP 工具：

- `mempalace_list_wings`：查看已有项目 `wing`
- `mempalace_list_rooms`：查看项目内 `room`
- `mempalace_get_taxonomy`：查看完整分类
- `mempalace_search`：搜索记忆
- `mempalace_check_duplicate`：写入前查重
- `mempalace_add_drawer`：新增稳定记忆
- `mempalace_update_drawer`：更新既有记忆
- `mempalace_get_drawer`：读取单条完整记忆

MCP 搜索规则：

- 项目内上下文：传 `wing`
- 指定类别：传 `wing` 和 `room`
- 跨项目搜索：不传 `wing`
- 通用经验：传 `wing="coding"`

脚本兜底时，先确认当前项目 `wing`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py wing --project-root .
```

如果要写通用开发经验，直接用共享 `wing`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py wing --shared
```

写入前先搜，避免重复沉淀：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py search --project-root . --query "认证迁移 决策" --results 5
```

如果用户要跨项目查历史，不要带 `--project-root`，改用显式 `--wing` 或 `--global`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py search --query "docker roundtrip" --global --results 8
```

如果只想查跨项目通用经验，优先查 `coding`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py search --shared --query "docker roundtrip" --results 8
```

## 写入方法

MCP 可用时，写入前先用 `mempalace_check_duplicate` 或 `mempalace_search` 确认没有同义重复。新增用 `mempalace_add_drawer`，更新已有条目用 `mempalace_update_drawer`。

通过 MCP 写入的 `content` 必须保持固定结构：

```text
标题: <短标题>
项目: <wing>
分类: <room>
关键词: <英文逗号分隔关键词>
项目路径: <项目绝对路径；通用经验写 (未绑定路径)>
更新时间: <YYYY-MM-DDTHH:MM:SS>

内容:
<稳定事实正文>
```

脚本兜底时继续使用下面命令。

短内容优先用 `--content`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py remember --project-root . --room commands --title "本地验证命令" --keywords "test,verify" --content "运行 python -m pytest tests/unit 验证核心流程。"
```

多行中文优先用 `--content-file`。PowerShell 把多行中文通过 `stdin` 管道给原生进程时，可能把中文降成 `?`。只有在 shell 已明确切到 UTF-8 或内容主要是 ASCII 时，才用 `--stdin`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py remember --project-root . --room decisions --title "Docker roundtrip 记录修复" --keywords "docker,roundtrip,record" --content-file C:\path\to\memory-note.txt
```

如果这条记忆应当被其他项目复用，就不要绑定当前仓库，改用 `--shared`，它会固定写到 `wing=coding`：

```powershell
python C:\Users\will\.codex\skills\project-memory-sync\scripts\mempalace_project_memory.py remember --shared --room pitfalls --title "Docker 日志回放坑点" --keywords "docker,logging,roundtrip" --content-file C:\path\to\memory-note.txt
```

写入规则：

- `title` 必须短、稳定、可复用
- `room` 必须选固定类别
- `keywords` 用英文逗号分隔，写最小必要词
- `content` 直接写结论、位置、命令、限制
- 同一项目下，`wing + room + title` 视为同一条记忆；再次写入会覆盖更新，不会无限堆重复版本

如果为了 `--content-file` 临时创建了文件，删除时只能用 `safe-del`。

## 搜索与回答

MCP 和脚本搜索结果都需要自己提炼后再回复用户。

- 需要项目内上下文：优先带当前项目 `wing`
- 需要跨项目通用经验：优先带 `--shared`，即只查 `wing=coding`
- 需要跨项目经验：用 `--global`
- 需要限定类别：补 `--room`
- 回答时明确说清楚命中的 `wing`、`room`、`title` 和关键结论

## 补充索引现有资料

如果用户明确要求把当前仓库代码、文档或导出对话一起纳入记忆，再用 MemPalace 原生命令：

```powershell
mempalace mine . --wing <wing>
```

如果是聊天导出：

```powershell
mempalace mine <dir> --mode convos --wing <wing>
```

只有在用户明确需要索引原始文件时才运行 `mine`。平时沉淀稳定项目事实，优先用本 skill 自带脚本直写 drawer。

## 参考

需要补看命令约定、房间约定或脚本输出结构时，再读：

- `references/mempalace-workflow.md`
