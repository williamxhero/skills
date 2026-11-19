# Pi 原生进程执行适配

这是工具映射参考，不是新调度器、Skill／hook API 或全局配置。先按实际入口的兄弟技能定位规则读取真实技能。本文供 implement-spec、专项资源和最终 code-review 的调用方加载；执行记录只保存指针和证据，任务前沿仍以 tracker 的 tickets／原生依赖为准。

## 能力预检与新上下文

必经能力和已知必需权限先核验；只在进入条件分支时探测其专属命令、环境或角色，不绕过完整闭包的必需门禁。记录已通过探针的环境／cwd、工具／版本、权限及角色要求；仅同条件复用。任一条件变化刷新相关探针；探针复用不是角色复用，实际角色仍须 fresh。

1. 读取当前安装版本的 Pi CLI／skills 文档并运行实际 `pi --help`，解析可执行文件、provider/model 和角色所需工具。不要把扩展的端口参数 `-p` 当成内建 print；使用完整 `--print`。不假定存在 Agent、Task、spawn_agent 或 Skill 工具。
2. 在目标工作目录启动新的原生 Pi 进程，使用 `--no-session`，不传 continue/resume/fork；每个角色／ticket 一次独立进程。用只读探针要求模型通过工具回报 cwd、项目指令和 tracker 身份；验收工具事件、输出与退出码，而非仅“进程启动成功”。必需角色不能工作则 blocked。探针不替代后续真实角色执行。
3. 原生进程可通过 shell 的进程启动能力并行／后台运行；调用方保存 PID、命令、cwd、任务文件、stdout／stderr／exit code，等待终止并读取结果。一个进程终止后才计其角色完成；超时／中断保留日志和工作树，调查实际状态后恢复，不推断成功或释放容量。无可用工具时不能用同一上下文伪装 fresh。

示例是 CLI 参数形状，先将变量解析为真实绝对路径／可用值，不原样运行占位符。PowerShell 中可切换 cwd 后前台执行；需要并行时分别用 `Start-Process`，指定 `-WorkingDirectory`、输出重定向和 `-PassThru`，等待后读取进程退出码和日志。注意该版本 shell 的参数引用，含空格路径先验证：

```powershell
Push-Location $RoleWorktree
try {
    & $PiExecutable --no-session --print --mode json --provider $Provider --model $Model --tools $RoleTools "@$AbsoluteTaskFile" 1> $StdoutFile 2> $StderrFile
    $RoleExitCode = $LASTEXITCODE
} finally { Pop-Location }
```

`--mode json` 提供工具／消息事件；终态文字和零退出码都不是验收证据的替代品。任务／日志默认放仓库外的任务专属 scratch。读写任务只授予本角色的路径和动作。工具 allowlist **不是安全沙箱**，shell 本身有写能力；仍需明确授权、prompt 边界和调用后 diff／归属核查。显式按需排除 MCP／扩展工具，不能把未限制的外部工具当作只读环境。

## 角色契约

- **Implementer**：独立 worktree／分支，核对当前 SPEC 集成分支祖先和干净状态；不重置有成果的分支。若落后或偏离，安全合并集成 tip 或建新工作树并保留原成果；禁止破坏性 reset。按 B2／B5 执行所选源码／文本场景／代码验证；可执行行为才按适用公开 seam 调用实际 tdd 及 tests／mocking，不为纯文字做行为 RED。报告前合入集成 tip 并验证，提交前执行 bundled verification 适配及正文；给出 diff、提交、命令／结果与证据指针（适用时含 red／green）及未完成项。不关闭或清理，不夹带其他线程 WIP。
- **Merger**：新的上下文，核验 ticket 范围、来源、祖先、测试证据与干净状态，再正常合入集成分支并验证；冲突时读取实际 resolving-merge-conflicts。不依赖实现者成功声明，不强推／覆盖 WIP，不提前关闭。
- **专项 reviewer**：新进程只收该 reviewer 完整上游 persona 和精确范围、proof tests、结构 pins、规范；只读输出。按 reviewer-dispatch 的真实容量／失败规则处理，注明专项允许的 inline fallback；这项许可不传递给最终独立审查。
- **Compound Full Related Docs Finder／history synthesis／grounding**：分别新上下文，传完整所需合约（不是相对路径的摘要）。finder 与 history／父级 drafting 按上游并行；等待全部输入后写入。finder／historian 仅本次 scratch 输出；grounding 只读回报。每个 prompt 内联 compound adapter 的全局词汇替换和写入边界；不得因上下文不足静默切 lightweight。
- **最终 Standards／Spec**：按实际 code-review 完整流程并行新进程，分别传规范及完整 smell baseline／原 SPEC，固定基点和候选，不互相共享发现。调用方聚合两个轴而非自己扮演 reviewer；任一必需 reviewer 不可运行则 blocked，不能援用专项 inline 降级。

角色可读项目指令和必要参考，但不得继承本会话历史或复用做过另一角色的上下文。记录真实模型与实际机制，不称 CLI 为一个不存在的 subagent API。

## Shell／脚本与路径

`Read/Grep/Glob` 映射实际 `read/grep/find`；Git／gh／Python 用当前 shell 工具执行并检查完整输出及退出码。所有技能相对资源先解析成绝对路径。上游 `SKILL_DIR` 在此指 **instructions.md 所在目录**，不指当前仓库或外层包装目录。

进入 Compound Full 后，其 Bash／Unix utilities 和 Python 3 必须实际探测：`bash --version`、`python -c "import sys; print(sys.version); assert sys.version_info.major == 3"`，并在所选 Bash 内验证 `git`、`python3/python/py`、`id`、`head`、`od`、`tr`、`xargs`、`mktemp`、`chmod`、`date`、`find`、`stat`、`awk`、`sed`、`tail` 等分支命令可运行、目标路径可读。Windows PowerShell 可用不代表 Bash 存在或能访问 Windows 路径；WSL／Git Bash 路径须分别验证，保留 upstream scratch 的所有权／非 symlink 保护。缺必需能力则记录具体阶段和最小继续条件，不假造 PowerShell 等价脚本；上游明确的 packs／history synthesis／validator 降级只在其原条件下使用并披露。

packs-resolve 可能访问声明的远程 pack 并写缓存。预检其配置与授权，未获网络／缓存写授权则按上游 unresolved 路径仅查本地 solutions 并报告，不隐式扩大权限。docs_root 仍只读 config.yaml 并做 repo-contained／non-root／非 .git 校验。
