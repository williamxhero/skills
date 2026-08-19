# MemPalace Shared Project Memory

## 共享模型

- 一份共享记忆：`~/.mempalace/palace`
- 一个项目一个 `wing`
- 一个记忆类别一个 `room`
- 一条记忆一组 `关键词`

## 脚本入口

Codex 已配置 MemPalace MCP，日常读写优先使用 MCP 工具。脚本是兜底入口，主要用于当前会话尚未加载 MCP，或需要按 `--project-root` 自动维护项目到 `wing` 的映射。

MCP server 配置：

```toml
[mcp_servers.mempalace]
command = "python"
args = ["-m", "mempalace.mcp_server"]
```

当前机器的 `python` 命令已修复到 Python 3.13；新 shell 可直接使用：

```powershell
python -m mempalace.mcp_server
```

脚本兜底入口：

- `scripts/mempalace_project_memory.py`

支持命令：

- `wing`：解析当前项目对应的 `wing`
- `remember`：新增或覆盖一条稳定项目记忆
- `search`：按项目或全局搜索记忆

## Wing 规则

- 默认从项目根目录名推导
- 推导结果会持久化到 `~/.codex/mempalace-project-memory/projects.json`
- 同一路径后续复用同一个 `wing`
- 需要显式指定时，用 `--wing`
- 如果是很多项目都能复用的开发经验，用 `--shared`，固定映射到 `wing=coding`

## 共享经验

- `--shared` = `wing=coding`
- 共享经验不要绑定仓库路径
- 优先记录可迁移的决策、命令、约束、坑点
- `safe-del` 只约束磁盘文件删除；Docker rm、Mempalace 删除 drawer 等软件内部删除不在此规则内

## MCP 读写流程

读取优先用：

- `mempalace_search`
- `mempalace_list_wings`
- `mempalace_list_rooms`
- `mempalace_get_taxonomy`
- `mempalace_get_drawer`

写入优先用：

- `mempalace_check_duplicate`
- `mempalace_add_drawer`
- `mempalace_update_drawer`

搜索规则：

- 项目内上下文：传 `wing`
- 指定类别：传 `wing` 和 `room`
- 跨项目搜索：不传 `wing`
- 通用经验：传 `wing="coding"`

写入规则：

- 新增稳定事实前先查重
- 已有同标题或同语义条目时更新，不重复新增
- 内容保持固定文档结构，保证脚本、CLI、MCP 搜出来都能直接读

## Room 建议

- `overview`
- `decisions`
- `constraints`
- `commands`
- `behavior`
- `pitfalls`
- `handoff`

## Remember 文档结构

脚本写入的 drawer 内容是固定结构：

```text
标题: ...
项目: ...
分类: ...
关键词: ...
项目路径: ...
更新时间: ...

内容:
...
```

这样做的目的：

- 结果可直接给人看
- 标题和关键词能参与搜索
- 不依赖仓库内额外记忆文件
- 共享经验可以稳定落到 `coding` 下

## 内容输入建议

- 短内容：用 `--content`
- 多行中文：用 `--content-file`
- `--stdin`：只在 shell 已切到 UTF-8 或内容主要是 ASCII 时使用

## CLI 备用命令

只有在用户明确要把原始项目资料放进 MemPalace，或 MCP 与脚本都不可用时，再用：

```powershell
mempalace mine <dir> --wing <wing>
mempalace search "query" --wing <wing>
mempalace wake-up --wing <wing>
mempalace status
```

## 适用边界

适合：

- 跨 thread 项目记忆
- 历史决策回溯
- handoff 沉淀
- 项目内固定命令、约束、坑点复用

不适合：

- 临时草稿
- 未确认结论
- 一次性调试日志
