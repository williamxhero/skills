# SPEC #1 — acceptance evidence index (not Done)

Checked against the originating [publication snapshot](specs/20261117-pi-engineering-to-done-workflows.md), whose 30 checkbox criteria and 12 scenarios are reproduced verbatim below. Live GitHub #1 and #5 were read back OPEN and bodies matched the saved inputs. This document is an evidence index, not another tracker, executable state machine, approval, or Issue-close record.

**Current disposition: source candidate + STATIC checks + bounded native-model observations; full acceptance pending/blocked.** No final independent review, outer full-diff simplification/capture decision, main merge, push, remote equality, managed deployment or closure is claimed. A row marked PASS is restricted to its stated observed boundary, not all scenarios in that feature. No “all acceptance pass” conclusion is justified.

## Evidence/version/command key

External evidence package name: `skills-1-20261117` (private local agent work area, deliberately not a Public-repository link). `runtime/`, `reports/`, `research/` below are relative to that external package. Raw JSONL may include private paths and thinking; retain externally, redact before any publication. This index publishes observations only, not raw prompts/logs.

- **S — STATIC:** source candidate `fcb3e6d` (#2–4 integrated) plus ticket #5 handoff changes. `python -m unittest discover -s tests -p 'test_*_static.py' -v`: 14 tests OK, exit 0, `reports/ticket-5-all-static.txt`; portability red/green in `ticket-5-red-portability.txt` / `ticket-5-green-portability.txt`. Exact snapshot SHA256 `f2a06c3eceb6bf9cfb012e2cce7704739b6ce5ce8883571594d05c516c0de1e7`. Full per-file upstream hashes are checked against shipped provenance, licenses and selected-directory copies. These are not model/platform tests.
- **L — inherited actual Pi/local file tracker:** spec source `d768c5d`, integration `499972d`. `runtime/observations.md`, `simple` / `repeat` / `missing` `-events.jsonl`, `-result.json`, `-stderr.txt`, and `run-simple.cjs` / `run-missing.cjs`. Native launcher JS behind pi.cmd, Pi 1.5.2, cpa/gpt-6.1-sol medium, fresh no-session JSON with extensions/MCP/discovered skills off and explicit staged skill. Original task/argv/cwd in result files. 2274 / 1179 / 201 events respectively; each settled non-aborted, final stop, exit 0. Missing is an expected blocked result, not successful publication. Independently re-inspected authoritative records, calls, persisted single local issue and unchanged greeting `48656c6c6f0a`; full staged spec entry bytes equal current source. No rerun cost, no upgrade to GitHub evidence.
- **A / G / I / M / N — new actual native Pi observations:** source `fcb3e6d`, explicit `pi.cmd` 1.5.2; exact commands/fixtures in external `runtime/run-ticket-5.py`. [Portable command shape and bounded inputs](../tests/runtime.md). Every case launched in an independent external fixture cwd, `--no-extensions --no-mcp --no-skills --skill <absolute staged entry> --no-prompt-templates --provider cpa --model gpt-6.1-sol --thinking medium --tools read,powershell,grep,find,ls --no-session --mode json` plus native `/skill:<name>` request. No installed resources were rewritten. `ticket-5-staging-hashes.json` captures actual copied dependencies. Source wrappers/resources match staged bytes; ticket #5 changes no entry/resource bytes, so these and L retain that bounded freshness. Not CC Switch deployed.
- **A — architecture no-change:** `runtime/ticket-5-architecture-{events.jsonl,result.json,stderr.txt}`, 3606 events, exit 0, final stop, settled non-aborted; fixture baseline `b8229b6a858f288feece7f8948428707d90e7701`. Actual `python -B -m unittest -v test_greeting`: 1 test, 2 assertions, exit 0; product diff empty. `ticket-5-architecture/architecture-evidence/` holds report, baseline output, distinct native probe and explorer tasks/streams/exit files. Independently inspected both actual role streams and zero exit files, not only parent prose. No candidate → no fabricated diagrams/publication.
- **G — missing production facts/authorization:** `runtime/ticket-5-architecture-blocked-*`, 982 events, clean fixture, final stop / settled / exit 0. Actual inventory/Git reads, C1 blocked, no explorer/candidate/no-change claim.
- **I — singleton:** `runtime/ticket-5-singleton-*`, 531 events, final stop / settled / exit 0, clean fixture; actual Pi with **SIMULATED** tracker P/A/B from fixture JSON. A OPEN/failed/undelivered, B blocked and ticketless; real Git readback, no writes or B ticketing/code. Stopped B2, does not prove B3 onward.
- **M — implementation missing shared sibling:** `runtime/ticket-5-missing-shared-*`, 313 events, final stop / settled / exit 0; two actual ENOENT reads from explicit missing root; no fallback/writes, blocked before B1.
- **N — no-code conditional phase:** `runtime/ticket-5-no-code-*`, 551 events, final stop / settled / exit 0; fixture baseline `8fd015a46b1a4233fb9fe71e513405da9bcf7b01`, only NOTES.md prose diff. B6 adapter-before-original instructions and provenance hash read, no reviewers/edits, B7 not triggered. Prior integration is explicitly SIMULATED and phase-only authorization does not establish a full implementation run. No-code here does not exempt repository Python tests from outer simplification.
- **R — independent event/source inspection:** `runtime/inspect-ticket-5.py` → `ticket-5-inspection.json`; stream SHA256, tool paths/commands, final authoritative message_end/agent_settled and exits, child sessions, staged/current byte equality. All eight parent streams parsed; no terminal transport failures observed. Native JSON mode can exit 0 on error/abort: transport completion is evaluated separately from expected blocked/no-change/success behavior. A clean transport is never itself task PASS.
- **H — real GitHub success testing: BLOCKED:** requested private isolated sandbox authorization has no confirmed reply in supplied execution record; no repo created, formal project tracker not used as sandbox, no GitHub writes by #5. Single/multi publication, label/native parent/dependency readback and repeat idempotence remain unrun. Read-only live #1/#5 bodies in `runtime/ticket-5-skills-{1,5}-readback.json` are identity checks, not acceptance publication tests.
- **D — CC Switch: SOURCE RESEARCH / STATIC ONLY:** `research/findings.md`, inspected source `a1216b7e359466be98f3c783cc290e7040de26d4`, not running binary identity. [Source management](source-management.md) records selected-directory copy, branch archive/update, ZIP skip/replacement and backup/restore facts. No UI import/update/rollback, client link/contents, reload or deployment authorization/test performed.
- **O — outer execution evidence / pending final stages:** `execution.md`, `reports/ticket-{2,3,4}.md`, `reports/merge-{2,3,4}.md`; source commits `d768c5d`, `d66816e`, `3c00b95`; integration `499972d`, `7304a24`, `fcb3e6d`. Actual original model role logs exist in the execution package, but #5 does not convert handoff prose into final independent-review or complete candidate workflow proof. Outer orchestrator owns stability/capture/final review, integration, remote delivery and closure. Consult ticket #5 report for final handoff commit and fresh postcommit checks.

No large reusable launcher was added to this repository. Scenario launch/inspection commands are tiny external evaluation aids, not a second workflow engine. Shell capability is not a sandbox. Interactive `/reload` and global installed name conflicts were not tested by one-shot explicit-source command expansion.

## 30 acceptance criteria

| ID | Exact originating requirement | Evidence/version/command key & kind | Status | Observed scope / remaining reason |
| --- | --- | --- | --- | --- |
| A01 | 三个入口在 Pi 中实际可发现、可读取、可执行；不存在同名冲突或失效的共享规则指针。 | STATIC S；实际 Pi L、A、G、M；部署 D 未运行 | PENDING | 三个显式原生命令已展开并读取实际来源，共享指针可读／缺失有观察；尚无交互 reload／管理式发现及全局同名冲突证据。 |
| A02 | 普通需求、信息完整的简单需求都实际执行规格级 grilling；没有基于任务简单而跳过的路径。 | 实际 Pi L；STATIC S | PENDING | 简单完整需求确有原 grilling 与 fresh explorer；普通需求的独立观察尚缺。 |
| A03 | 自动推荐按依赖逐轮推进，不等待常规逐题答复，不设置机械问题数量下限；关键决定进入规格。 | 实际 Pi L；STATIC S | PENDING | 一轮全部自动采纳／决定入本地 SPEC 已见；多轮依赖及新事实反转尚未运行。 |
| A04 | 能查清的事实先调查；无法验证的事实明确标注，不用推荐答案伪造事实或授权。 | 实际 Pi L、A、G、M | PENDING | 本地事实调查、真实缺失与授权不足阻塞已见；不能推广为所有成功分支。 |
| A05 | 单独原版 grilling 和原架构调查仍保持自己的可用交互入口，本地自动适配的差异有记录。 | STATIC S；隔离原始依赖哈希 R | PENDING | 适配差异明确，原始依赖仅复制未修改；原版单独交互未实际运行。 |
| A06 | 规格只由 to-spec 按原模板生成；每个新 SPEC 包含本规格末尾完整交付段落。 | STATIC S；实际 Pi／本地 tracker L | PENDING | 原版 to-spec 已实际读取、本地正文含原模板结构与完整交付段落；GitHub 新 SPEC 尚缺，非所有发布路径已验。 |
| A07 | 单 SPEC、总 SPEC 加子 SPEC 两种组织均有真实发布和回读证据；GitHub 原生关系和正文清单一致。 | 真实 GitHub H 未运行 | BLOCKED | 隔离目标创建／写入授权未确认；没有单／多 SPEC GitHub 发布及原生关系证据。 |
| A08 | 重复执行规格发布不会重复建票或建立无关关系；遗留已有 tickets 的兼容规则得到保留。 | 实际 Pi／本地 tracker L-repeat；STATIC S；H blocked | PENDING | 本地重复执行复用一份 SPEC；GitHub 幂等与遗留 tickets 恢复仍未运行。 |
| A09 | tracker 不可达或关系无权创建时明确未完成；本地草稿不被报告为发布成功。 | 实际 Pi M、G；STATIC S；H 未运行 | PENDING | 缺资源／事实阻塞已见，但尚无实际不可达 tracker、无关系权限或结果未知的完整观察。 |
| A10 | 单独规格入口发布后停止，不启动拆票、编码、Git 集成或推送。 | 实际 Pi／本地 tracker L、L-repeat | PASS（本地观察） | 回读本地 SPEC 后停止，原 greeting 未变、无 to-tickets／实现／Git 交付调用；不声称 GitHub 成功。 |
| A11 | 多 SPEC 场景中任一时刻只有一个当前 SPEC；后续 SPEC 的拆票也等待前项交付关闭。 | 实际 Pi＋SIMULATED tracker I | PASS（模拟边界） | A OPEN／验证失败／未交付，B 无票；读回真实 fixture Git 后 B 未拆票编码。只验证停止门禁。 |
| A12 | 每个当前 SPEC 在拆票前完成实施级 grilling 复核，复用既有决定，不把每个 ticket 变成重复访谈。 | STATIC S；实际 I 仅到 B2 | PENDING | 拆票前复核契约存在；I 在 B2 阻塞，没有实施级 grilling 成功证据。 |
| A13 | tickets 真实发布、依赖正确、已有合格项复用；没有先编码后补票的成功路径。 | STATIC S；外层原生图执行记录 O | PENDING | SPEC #1 的真实票据图由外层建立回读，不等于候选入口自主复用执行。H 及原生图恢复未运行。 |
| A14 | implementer、merger 和独立双轴审查使用支持的真实子 Agent 机制；独立工作目录和阶段结果可核验。 | 实际 Pi A 的 fact explorer；外层角色记录 O | PENDING | fresh probe／explorer 已独立核验；#2–4 implementer／merger 有外层记录，最终 Standards／Spec 由外层另行执行，不在本 ticket 委托。 |
| A15 | TDD 通过约定公开接口逐项红绿推进；已有测试接口授权被复用，新实质变更有记录。 | STATIC 红绿 S、外层 #2–4 日志 O | PENDING | 本 ticket 公开包装 seam 有一项 red→green；非候选完整实施 TDD 行为验收。 |
| A16 | 稳定后的简化在最终 code-review 之前实际发生；纯技能 Markdown 变更按上游规则返回无代码可简化，不启动无意义审查。 | 实际 Pi N；STATIC S；外层稳定检查未运行 | PENDING | 纯 Markdown B6 实际读取适配／上游并返回 no-code，无 reviewers；当前 SPEC 含 Python 测试，仍需外层完整 diff pipeline，不能按纯文档跳过。 |
| A17 | 简化不删除或弱化证明测试、安全、兼容或已确定结构约束；失败时修复或撤销责任变更，不削弱断言。 | STATIC S；N 无代码路径 | PENDING | 证明测试保护有契约，本 ticket 未弱化既有测试；简化造成回归再恢复的真实场景未运行。 |
| A18 | 只有已解决且验证的错误认识触发 ce-compound；触发后由技能判断载体或无需写入，不能调用方先以不值得写文档为由省略。 | STATIC S；实际 Pi N 仅 not triggered | PENDING | 未提供适格错误认识的 B7 不触发已见；已验证纠错后必须调用 capture 判断的场景未运行。 |
| A19 | 经验捕获明确 non-interactive、一次一个经验，不意外启动 refresh 或另开 PR，不平行维护冲突词表。 | STATIC S／provenance；Full capture 未运行 | PENDING | non-interactive／单项／词汇替换边界已静态核对；未将文档检查当 capture 执行。 |
| A20 | 最终 Standards / Spec 独立审查覆盖简化与经验回写后的差异；实质修复使相关证据失效后会更新验证或复审。 | STATIC S；外层最终审查 O 待执行 | PENDING | 最终独立双轴审查／修复后证据刷新不在本 ticket 自审，等待外层。 |
| A21 | verification-before-completion 在完成声明前使用真实命令与输出；构建、测试通过、需求满足、远端同步各有对应证据。 | 实际静态命令 S；实际 Pi 事件检查 R；平台交付未运行 | PENDING | 本 ticket 已加载实际 verification 并运行检查；全部构建／需求／远端仍需逐项证据。 |
| A22 | 主干集成、合并后验证、安全清理、push、远端核验成立后才记录并关闭；子技能、PR 自动关闭不能绕过门禁。 | STATIC S；交付 O 未执行 | PENDING | 本 ticket 无 main merge／cleanup／push／关闭权限，保留分支成果；不能宣称 B10–B11 通过。 |
| A23 | 架构候选有实际代码或运行依据、扫描范围和改进前基线；不把图形美观或减少文件数当作普遍收益。 | 实际 Pi A，fixture b8229b6 | PASS（有界无候选） | 真实两文件／测试基线、fresh explorer 与 deletion test 支撑排除候选；未测试有收益候选及图示。 |
| A24 | 架构入口只推进本次推荐范围，委托同一规格和实施链；无候选时正常 no-change，事实或授权不足时明确 blocked。 | 实际 Pi A、G；STATIC S | PENDING | 有据 C3 no-change 与 C1 缺事实／授权 blocked 已见；selected→同链交接未运行。 |
| A25 | 改进收益、兼容、恢复和迁移要求在关闭前已验收，外层回读不能替代缺失的交付前证明。 | STATIC S；有收益交付未运行 | PENDING | 关闭前收益／兼容／恢复／迁移契约存在，未提供实际交付收益证明。 |
| A26 | 三入口不会自动递归调用架构改进、重扫全仓或在已交付后不断追加重构。 | 实际 Pi L、A、G、I、M、N | PASS（所观测终态） | 这些观察均停在声明门禁，没有递归扫描／新重构；不是对未执行成功链的普遍保证。 |
| A27 | 中断恢复不重复外部写入、不误关未交付 Issue、不清理未合并成果；完成记录能够重建当前 SPEC 和证据。 | 实际 Pi I、L-repeat；STATIC S | PENDING | 模拟失败状态恢复／本地去重已见；未知外部写入、误关、未合并 WIP 交付恢复尚缺。 |
| A28 | 上游依赖版本、适配差异、共享目录影响及 Pi 加载方式可追溯；缺少必需依赖时不能报告真实端到端通过。 | STATIC S；实际 Pi M、R；CC Switch 只读研究 D | PASS（来源／阻塞观察） | 52 原文件及通知、固定版本／适配／跨目录影响可追溯；实际缺必需资源停在入口，不报告端到端成功。D 不是安装证明。 |
| A29 | 三入口源码在用户指定仓库版本化；现有入口初始导出有逐文件哈希，第三入口的草案状态可见；后续部署仍经 CC Switch 管理，不改用另一套安装器或客户端直连仓库。 | STATIC S；历史 snapshot；实际源码分支 O；D 未部署 | PENDING | 源码候选已版本化、历史快照未改、第三入口未部署可见；本 ticket 未推送当前版本，CC Switch 导入尚待授权。 |
| A30 | 本轮归档与后续实施的成果、未完成验收和实际安装状态分别记录；没有把仓库创建或 Issue 发布误记为三流程整合已经完成。 | 本矩阵、README、源码管理；R／S／O | PASS（报告边界） | 分别标记历史归档／当前源码／有限运行／未完成／未安装；SPEC／tickets 未因此关闭，非完成交付声明。 |

## 12 required scenarios

| ID | Exact originating scenario | Evidence / kind | Status | Observed scope / remaining reason |
| --- | --- | --- | --- | --- |
| S01 | 小而明确的需求：仍执行 grilling，自动采用推荐后由 to-spec 发布，再停止。 | 实际 Pi L | PASS（本地观察） | 完整小需求仍读取 grilling／to-spec，发布本地文件后停止；H 未验证。 |
| S02 | 多轮依赖与新事实：下游问题等前置决定；新事实推翻推荐后更新决定，而不坚持旧答案。 | STATIC S；L 仅单轮 | PENDING | 没有多轮新事实反转的实际运行。 |
| S03 | 两个顺序子 SPEC：第一项失败或未关闭时，第二项不拆票、不编码。 | 实际 Pi＋SIMULATED tracker I | PASS（模拟边界） | A 失败且 OPEN，B 保持无票无代码；不代表完整子 SPEC 交付／切换。 |
| S04 | 已有 tickets 的恢复：复用任务图，回读外部结果，重复执行不产生重复票据。 | 实际 Pi L-repeat、I；H 未运行 | PENDING | 本地 SPEC 去重与模拟现有 A1 回读不等于真实票据图恢复／原生依赖幂等。 |
| S05 | 简化后回归失败：恢复或修复对应变更，保留证明测试，不以原来通过的测试报告完成。 | STATIC S | PENDING | 未注入简化后回归失败；本 ticket 未进行破坏性简化或弱化测试。 |
| S06 | 错误认识经修复并验证：调用 ce-compound 判断；没有适格经验时不生成装饰性文档。 | N 仅无触发；STATIC S | PENDING | 没有适格经验的 Full capture/no-write 实际运行，不能算已调用。 |
| S07 | 审查修复改变最终候选：验证和审查覆盖最终差异，不重复完整审查无变化内容。 | 外层 O | PENDING | 最终审查与修复由外层组织，本 ticket 未伪造 reviewer 或重复审查。 |
| S08 | 无收益架构候选：有依据地 no-change；有收益候选则先规格后实施，收益在关闭前验证。 | 实际 Pi A、G；selected 未运行 | PENDING | 无收益 no-change 和缺事实 blocked 已观察；有收益先规格后交付仍缺。 |
| S09 | 缺失必需技能或子 Agent 启动失败：停留真实阶段，不声称已运行独立审查。 | 实际 Pi M、L-missing；A probe 成功 | PENDING | 缺必需共享资源的停止路径通过；必需子 Agent 启动失败路径未单独注入（历史启动故障不是新候选成功验收）。 |
| S10 | tracker 不可达、关系失败或外部结果未知：保留草稿或已发布成果，源头回读后再决定重试。 | H 无隔离写入授权；STATIC S | BLOCKED | 未模拟平台成功；不可达／关系失败／未知结果恢复需获准隔离目标或明确模拟场景。 |
| S11 | 用户已有无关 WIP 与其他线程分支：不得纳入本次自动提交、清理或全项目交付。 | 本 ticket Git 状态／限定提交 O | PENDING | 本 ticket 未写其他线程成果；尚未在候选完整交付路径注入无关 WIP／分支。 |
| S12 | 本规格本身是技能文档工作：ce-simplify-code 正确判断非代码范围，技能行为仍经独立流程验证。 | 实际 Pi N；STATIC S；外层 O | PENDING | 纯 prose fixture 的 no-code 路径通过；本 SPEC 新增 Python 测试是代码，外层全差异简化及最终独立行为审查尚待完成。 |

## Smallest continuation conditions

1. Confirm explicit authorization for an isolated GitHub success target before any repository creation or test issue/relationship writes; then run H single/multi/readback/idempotence and failure/recovery cases there. Do not silently use the formal tracker or an unrelated project. No reply is not permission.
2. Outer orchestrator completes remaining bounded model/failure scenarios and required full-diff specialist stages, then independent parallel final Standards/Spec review on the final candidate. Record failures and refresh affected evidence rather than retrying blindly or treating this matrix as completion.
3. Actual CC Switch installation/update/rollback needs separate user authorization and running-version verification; source-only development did not authorize deployment. Resume D with independently confirmed restorable backups and the supported UI path, then verify managed resources, projection and reload. Until then report source packaging only.
4. Only outer authority may complete main merge/postmerge checks, owned-resource cleanup, normal push/remote equality and tracker completion-record readback/closure under the SPEC's exact completion method. No ticket/SPEC is closed by this handoff.
