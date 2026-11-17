# Local adapter — ce-simplify-code pipeline

**每次先读本适配，再完整读取并执行 [instructions.md](instructions.md) 及其触发参考。** 本包装入口仅支持 `mode:pipeline`，范围固定为唯一当前 SPEC 的基点→候选精确差异；不激活 standalone／全仓整理。完整上游目录保留是为了参考完整性，不是启用另一条主链。默认不传 size-cutoff；若显式设置，先读 references/single-pass.md，仍按其规则 review 而非省略。

来源：EveryInc/compound-engineering-plugin，commit `a3d6a8ee41a404b74391ee278423b52890b8920d`，`skills/ce-simplify-code/SKILL.md`，SHA256 `391B434B9DD42C10E2FF7D56828BD40B9FFF4F412237ECAB7EBCCD41AF49A7CA`。12 个原文件与根 MIT Every 2025 [LICENSE](LICENSE) 均保留；逐文件 source path／hash 见 [provenance.json](../provenance.json)。唯一上游变更是 SKILL.md 主文件改名，字节不变。`SKILL_DIR` 锚点为本目录。

## 贯穿所有参考的覆盖契约

- 在实现稳定后、最终 code-review 前执行一次本阶段，不在每次 red→green 编辑后运行。不扩大到其他 SPEC、vendor、配置、生成内容或用户 WIP。修复造成证据过期时仅针对受影响内容处理，不开启无界整理循环。
- 遵守原 no-code preflight：技能／prompt／agent Markdown 是 prose，不是代码。无代码或仅机械 churn：报告 `nothing to simplify`，不派 reviewers。混合范围只保留 authored executable code 和其 fixtures；Python 测试也是代码，不能因主体为 Markdown 就跳过它们。
- 保护输出、错误、side effects、顺序、安全／信任边界／数据丢失保护、兼容约定和 caller structure pins。传入全部 `proof-test:` 路径及证据；证明测试保持原样，含其 test-only 调用代码。身份不明则不删改任何当前新增／修改测试。简短不是成功指标。
- 真实 Pi 委托按 [pi-execution.md](../../pi-execution.md) 执行：完整 persona 原文、新上下文进程、只读、范围与约束，不能调用虚构 Agent／Task／spawn_agent。先读 reviewer-dispatch.md；按真实容量排队。只有上游明确允许的失败／无 primitive 情形才能 inline，并报告替代；不授权最终 code-review 自审。
- 修改后跑项目 typecheck／lint 及 blast-radius 测试；未配置明确记 n/a。简化导致失败则修复或撤销责任变更，不弱化断言。按上游 Step 5 汇报分类计数、跳过及检查；summary 仅供外层记录，不贴入 commit／PR。
