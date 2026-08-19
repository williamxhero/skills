---
name: crawler
description: 安全执行依赖已登录浏览器、在线 Jupyter/Notebook、网页接口或远端数据源的长时间数据爬取与归档，统一通过 Codex Chrome Extension 复用 Microsoft Edge Profile 1。用于用户要求爬虫、抓取、补历史数据、分批下载、断点续跑、浏览器自动化采集，或需要控制浏览器身份、完整性门槛、压缩格式、远端磁盘和 kernel 生命周期的任务；目标涉及 SuperMind 时必须继续使用 supermind-crawler。
---

# 爬虫

把爬取任务视为一条可恢复、可验证、资源有上限的数据管线。先确认入口和契约，再小批试跑；只有本地验证完成后才清理精确的远端任务目录。

## 先读取现场约束

1. 读取当前项目的 `AGENTS.md`、爬虫代码、测试、配置和运行文档。
2. 优先复用项目已有的归档策略、批处理器和校验器。浏览器交互优先复用 Codex Chrome Extension；不要因为项目里已有 CDP 启动器就继续走旧通道。
3. 对能由专用 connector、API 或 CLI 完成的数据操作优先使用该通道；只有登录态、可视页面或 UI 交互确实必要时才接管浏览器。
4. 在长任务前探测登录状态、页面入口、Notebook runtime、必要 API 和本地目标磁盘。
5. 若关键接口不健康、登录失效或数据契约不明确，先停止会写数据的步骤并报告证据。
6. 若任务涉及 SuperMind 研究页、Jupyter iframe、SuperMind API、`supermind_proj` collector 或 SuperMind 数据导出，先调用 `$supermind-crawler`，再进行任何浏览器、Notebook 或写数据动作。非 SuperMind 任务不要加载该专项技能。

## 固定浏览器身份

1. 所有爬虫都必须使用已安装 Codex Chrome Extension 的当前 Microsoft Edge `Profile 1` 会话。扩展名称里的 “Chrome” 不等于改用 Google Chrome。
2. 按 `chrome:control-chrome` Skill 选择明确的 Edge browser family，复用已有 Edge binding 和 tab。不要每个回合重新初始化浏览器连接。
3. 不要使用独立 Playwright、裸 CDP/remote-debugging-port、自建浏览器扩展或另一个自动化服务器接管会话。官方扩展文档提供的 `tab.playwright` 属于扩展通道，可以使用。
4. 若官方扩展连接失败，先按其 troubleshooting 流程恢复；仍不可用时报告阻塞并停止该分支。不得退回 CDP、独立 Playwright、Computer Use、其他浏览器或其他 profile。
5. 不要强杀、重启或另开 Edge 来获得控制权。复用当前会话，不读取 cookies、local storage、密码、profile 文件或 session store。
6. 为每个站点维护精确的页面 allowlist。通过扩展读取当前 tab，只从 allowlist 页面寻找目标 iframe 或应用上下文，不使用“第一个 tab/第一个 frame”。
7. 每次爬虫执行计划、开始前检查和最终验收都显式写出三项全局硬门槛：Codex Chrome Extension 已接管当前 Edge `Profile 1`、Google Chrome 未使用、独立 Playwright/CDP/自建扩展未使用。缺少任一项就立即停住该分支。

## 先做小规模闭环

1. 先运行只读 dry-run，确认日期范围、交易日或分页计划、目标目录和预计批次数。
2. 用一天或一页做 smoke run，走完“采集、压缩、下载、校验、远端清理、runtime 清理”的完整闭环。
3. 再按季度、月份或有界页数执行正式任务。让每批可独立验证和安全重跑。
4. 长任务持续输出简短状态，区分预检、身份解析、远端生成、本地下载、校验和清理阶段。

## 拒绝表面成功的残缺响应

1. 不把 HTTP 200、非空 DataFrame 或进程退出码 0 当作完整性证据。
2. 为数据集定义列、日期、代码格式、唯一性、最小合理行数和预期批次覆盖规则。
3. 从已确认完整的历史样本建立质量下界；观察到部分响应后提高门槛，不要为了让任务继续而降低门槛。
4. 对瞬时空响应、截断响应、429、5xx、超时和临时 Notebook 故障做有界重试。
5. 重试耗尽后 fail closed：不生成正式目录，不跳过缺失日期，不把部分文件标为完成。
6. 对外部版本化 API 冻结 `data_version`。中途版本变化时重跑整个依赖该版本的解析阶段，禁止混用新旧版本。

## 控制在线 Notebook 资源

1. 每个独立操作使用新 kernel，并在 `finally` 中 interrupt/delete。
2. 只清理未绑定其他 Jupyter session 的孤儿 kernel；不要影响用户或其他任务的 session。
3. 为 WebSocket 执行、kernel 创建、中断、删除和内容下载设置超时。WebSocket 提前关闭必须立即判为失败。
4. 同时只保留一个大型 provider/snapshot frame。每个交易日或有界批次后 `del` 引用并执行垃圾回收。
5. 不在在线 Notebook 累积长期归档。远端只作为短期 staging，正式数据保存在本地受控目录。

## 使用压缩 Parquet 归档

1. 表格数据使用压缩 Parquet；默认优先 SNAPPY。不要在在线工作区或完成的本地归档中保存 CSV。
2. 小型 manifest、质量报告、lineage 和控制文件可以使用 JSON。
3. 先写本地 `<range>.partial`，再验证必需文件、日期覆盖、行数、SHA-256、Parquet 首尾 `PAR1` 和实际 codec。
4. 验证通过后原子改名为正式目录；已验证的正式范围直接复用，不重复抓取。
5. 本地正式目录完成后，删除精确的远端 job/range 目录。失败时只清理当前任务的远端 partial 与本地 staging。
6. 清理前解析并打印绝对目标；禁止用宽泛根目录、未解析变量或跨 shell 拼接执行递归删除。

## 处理跨数据源身份

1. 先冻结主数据源快照，再用辅助数据源解决歧义，不让辅助 API 改写原始证据。
2. 保存候选项、比较输入、得分、选择结果、选择方法和辅助数据版本。
3. 只有明确的“无成员/无匹配”业务结果才能触发回退规则；健康、完整性、截断或版本错误必须中止。
4. 若项目约定“成员重合度唯一最佳，否则最小 ID”，严格按此执行，不凭名称相似度猜测。

## 完成验收

1. 核对所有预期范围存在，日期不重复、无缺口，且没有 `.partial` 或 CSV。
2. 重算 manifest 指定文件的 SHA-256，并读取 Parquet metadata 确认压缩 codec。
3. 报告请求起始日与首个实际数据日；节假日导致的差异不是缺口，但必须明确说明。
4. 复查远端任务目录为空、待清理队列为空、本任务创建的 kernel/session 为 0；其他 session 保持不动。
5. 通过 Codex Chrome Extension 复查 Edge `Profile 1` tab：仅保留 allowlist 页面；确认没有使用 Google Chrome、独立 Playwright/CDP 或自建扩展。
6. 运行相关单元测试和最小集成验证；只报告实际执行过的检查。
