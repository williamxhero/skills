# SuperMind 爬取约束

## 浏览器入口

- 项目：`D:\WILL\STOCK\supermind_proj`。
- 唯一启动入口：`https://quant.10jqka.com.cn/view/study-research.html`。必须先进入该页，等待用户登录完成，再等待 SuperMind Jupyter iframe 就绪。
- 使用 `$crawler` 规定的 Codex Chrome Extension、Microsoft Edge `Profile 1` 和现有 binding。禁止改用 Google Chrome、独立 Playwright、裸 CDP、remote debugging、自建扩展或其他浏览器自动化服务。
- 官方扩展暴露的 `tab.playwright` 是扩展内能力，不属于被禁止的独立 Playwright。
- 禁止打开或导航到 `http://data/`、`http://0.0.0.1/`。服务器文件系统的合法 `/data/...` 路径不受此限制。
- 连接后只在真实研究页内寻找 `supermind.10jqka.com.cn/notebook/user/.../(lab|tree)` iframe。不要把错误 tab、第一张页面或任意 frame 当作 Jupyter 环境。
- 不要直接导航到 notebook URL 或错误恢复入口；这种进入方式可能触发 Nginx `Forbidden` 或登录闪退。登录或 iframe 未就绪时停止写操作并等待用户处理。
- 启动与收尾都通过官方扩展读取 tab，关闭已存在的错误页，并确认最终只有真实研究页。
- 每次执行计划、开始前状态和最终验收都逐项明确：官方扩展已接管 Edge `Profile 1`、Google Chrome 未使用、独立 Playwright/CDP/自建扩展未使用、两个错误 URL 均未打开。缺少任一项都不算预检完成。

### 旧路径处置

项目中的 `scripts/start-edge-debug-supermind.ps1`、`EDGE_CDP_URL` 和基于 `chromium.connectOverCDP` 的 runner 属于禁用的旧兼容路径。不要因为这些脚本存在就运行，也不要创建、安装或维护另一套自建扩展。官方扩展无法连接时按 `$crawler` 规则停止该分支。

## 项目归档契约

每次修改或新增 SuperMind collector，先读取并强制执行：

- `AGENTS.md`
- `config/supermind-archive-policy.json`
- `docs/supermind-archive-policy.md`

collector 必须在写数据前加载并校验 `config/supermind-archive-policy.json`。配置缺失、字段不全、值不匹配或 collector 无法执行其中任一约束时立即失败，不得使用内置默认值继续运行。

关键值：

- `tabular_format=parquet`
- `parquet_compression=snappy`
- `max_in_memory_snapshot_frames=1`
- `kernel_lifecycle=fresh_per_operation_delete_in_finally`
- `remote_retention=delete_after_verified_local_download`
- 表格只写 SNAPPY 压缩 Parquet，不生成 CSV；JSON 只用于 manifest、质量和控制证据。
- 禁止运行仍写 CSV 的 legacy collector；先迁移再执行。

## 大批量表格任务

1. 先完成浏览器四项硬门槛，再检查登录、iframe、kernel/session、远端 job 根和本地目标磁盘；不要启动旧 CDP runner。
2. 加载归档策略并 fail closed。用 `get_trade_days(start, end)` 推导预期交易日，用 collector 计划和 manifest 推导每日期望批次与文件；不要写死历史样本数量。
3. 先 dry-run，再用一个交易日完成 smoke run。远端 job 根只包含职责明确的 `data/`、`manifest/`、`errors/`。
4. 2GB 在线内存下默认一次只处理一个交易日；单日仍过大时再按股票做有界批次。同一时刻最多保留一个大型 provider/snapshot frame，每批和每日写盘后 `del` 所有大型引用并执行 `gc.collect()`。
5. 每个独立操作创建 fresh kernel，在 `finally` 中带超时执行 interrupt 和 delete。只处理当前操作创建的 kernel；不要删除绑定其他 Jupyter session 的 kernel。
6. 整个任务只下载到唯一的 `<range>.partial`。每个交易日严格执行“远端生成 -> 写入 range staging -> 按 manifest/hash 做日级验证 -> 在 range staging 内记录该日已验证 -> 删除精确远端当日目录”。日级验证通过仍不是正式归档；不要在验证前删除远端当日目录。
7. 每日失败时只清理 `<range>.partial` 内该日未验证的临时文件和精确远端 partial；保留其他已验证日期和可诊断的 JSON 错误证据。重试耗尽后停止整个任务，不跳过该日。
8. 恢复时只复用 `<range>.partial` 内经 manifest/hash 重新验证通过的日期；日目录或非空文件存在不代表完成。
9. 只有全部预期交易日完成且全 range 验证通过，才把 `<range>.partial` 原子改名为正式 range。随后清空精确远端 job 根并完成 runtime、浏览器和完成门槛检查。

### Contents API

- 枚举目录时只读取目录响应中每个 item 的 `type` 和 `name` 来收集路径。不要先对每个 Parquet 请求 `content=1` 再判断它是不是文件。
- 实际下载时才逐文件请求 `content=1`，校验响应为 base64 后用临时文件名或日级临时子目录写入唯一的 `<range>.partial`。为每次请求设置超时，并使用有上限的重试次数、退避和总时限。
- `Failed to fetch`、请求 abort、frame detach、Nginx `Forbidden` 和 gateway timeout 只允许触发有界恢复：回到真实研究入口、重新确认登录和 iframe、重新取得 runtime 后从 manifest 断点继续。耗尽后 fail closed。
- 整日文件和 manifest/hash 验证通过并在 range staging 内记录完成后，才删除精确远端当日目录；禁止边下载边删除远端文件。

### 逐日验证

至少验证：

- `get_trade_days` 的预期交易日与 range/day manifest 完全一致，每日期望批次和文件全部存在且无额外日期。
- 必需列存在。1m K 线至少核对 `stock`、`datetime`、`trade_date`、OHLC、`volume` 和 `turnover`。
- 文件与 manifest 行数一致，日级总行数合理；`(stock, datetime)` 唯一，股票代码格式正确，每行日期与目录及 manifest 日期一致。
- 每个文件的 SHA-256 与 manifest 一致，首尾四字节均为 `PAR1`，Parquet metadata 的实际 codec 为 `SNAPPY`。
- 本地正式目录没有 CSV、`.partial`、缺失文件、未记录文件或失败状态。只检查“文件非空”不算验证。

### 长任务监督

- 把状态、错误、重试、已验证日期和完成标记写入持久日志或 JSON state。先持久化关键状态，再把 stdout 作为辅助输出。
- 前台控制通道退出时 stdout 可能报 `EPIPE`；捕获并降级 stdout 写入，不得因此丢失持久日志或把任务误判为完成。
- watcher 只监督进程、日志新鲜度和验证状态。生成结束、目录存在或单个 marker 都不是整体完成；恢复时从 manifest 和验证状态断点续跑。
- 重试和恢复次数必须有上限。耗尽后记录失败状态并停止，不自动安排睡眠。

### 完成门槛

按以下顺序完成，最后才写总完成标记：

1. range staging 已完成全量验证并原子提升为本地正式目录。
2. 所有 manifest、SHA-256、Parquet 结构、实际 codec、日期、文件、行数、唯一性和代码日期检查通过；无 CSV、`.partial` 或 pending cleanup。
3. 精确远端 job 根已清空，待清理队列为空。
4. 本任务创建或拥有的 kernel 和 session 均已清理为 0。其他 Jupyter session 绑定的 kernel 保持不动，不计入本任务清零门槛，也不阻塞本任务完成；可在验收报告中列出。浏览器四项硬门槛复查通过。
5. 将总完成标记持久化。只有此后才可安排睡眠。

### 1m 实例证据

`2025-08-01` 至 `2025-12-31` 的任务由 `get_trade_days` 得到 103 个交易日，远端 `data/` 生成并下载了 7,377 个数据 Parquet。二者只证明该范围的实际结果，不是其他日期范围的固定门槛；下次必须从 `get_trade_days`、股票批次计划和 manifest 推导期望值。

## 概念历史完整性

- `concept_classification` 最低完整行数固定为 3,400。
- 已确认最早完整快照最少 3,442 行；已观察到的瞬时部分响应为 0、547、1,750、3,290 行。
- 预检与每日采集均做三次有界重试；仍不足 3,400 时整批失败。
- 检查必需列、请求日期、股票代码格式、重复关系和市场范围。
- `.NQ` 行保留在不可变原始快照中，但不进入 SHSE/SZSE/BJSE 成员关系；在质量报告中计数。
- PIT 标记保持 `effective_date_approximation` 和 `knowledge_time_available=false`，不要虚构发布时间。

## MarketHub 歧义概念解析

- 默认服务：`http://yosef-server:8803`。
- 对同名多 source ID，读取范围最后交易日的 MarketHub 板块成员，与 SuperMind 同日成员比较。
- 唯一最高正重合得分选为 source alias；没有正的唯一匹配时选择数值最小 ID。
- 只有明确的 `CONCEPT_MEMBERSHIP_EMPTY` 才算“无板块成员”并进入最小 ID 回退。
- 健康、完整性、截断或版本一致性错误必须中止，不得伪装成空成员。
- 身份解析全程绑定同一 `data_version`。遇到 409 version mismatch 或最终 health 版本变化，重跑整轮身份解析。
- 对超时、429、5xx 做有界请求重试；409 向上抛给整轮版本重试。

## 已验证的故障防护

- Jupyter WebSocket 在执行结果前关闭：立即拒绝，不等待总执行超时。
- kernel interrupt/delete 卡住：使用 30 秒控制超时，并把未完成清理加入后续精确清理流程。
- 临时 `Forbidden`、frame detach、gateway timeout：刷新真实 SuperMind 页面后有界重连。
- MarketHub 在 10–13 分钟身份扫描中更新版本：丢弃整轮结果并从固定新版本重试。
- SuperMind 返回 HTTP 成功但行数部分：按 3,400 门槛拒绝并重试，绝不降低门槛。

## 概念历史专用校验

执行概念历史任务时额外运行：

```powershell
node --test scripts\test-run-supermind-concept-history-download.mjs
python -m pytest supermind_jobs\test_download_concept_history.py -q
```
