# ASR deliverable template

Use one corrected Markdown file per recording. For multiple recordings, add an index linking to each file and summarizing roles, duration, main topic, and remaining uncertainty.

```markdown
# 《录音标题》角色校对稿

## 录音与处理信息

- 原文件：`...`
- 音频：编码、采样率、声道、时长、大小
- 本地 ASR：模型、设备、计算精度、语言、beam、VAD、词级时间戳
- 恢复步骤：异常范围、独立窗口、是否关闭前文条件
- 外部服务：未调用 / 服务与模型及调用范围

## 角色识别

- **角色 A**：证据。**置信度**。
- **角色 B**：证据。**置信度**。
- 声学辅助质量与边界限制。

## 带时间戳的角色校对稿

**[00:00.0–00:12.6] 角色 A：** 校对后的内容。

**[00:12.6–00:30.0] 角色 B：** 校对后的内容。（约重复 4 遍。）

## 关键校对与不确定处

| 时间 | 初识别或疑点 | 校对结果 | 依据 / 置信度 |
|---|---|---|---|
| 00:10–00:12 | 同音候选 | 校对词 / `[听不清]` | 发音、上下文与全局意图；高/中/低 |

## 内容与意图理解

1. 主要交际意图。
2. 内容脉络。
3. 各角色的目标、立场或体验。
4. 事实边界：哪些只是说话者转述或观点。

## 覆盖与质量说明

- 转写覆盖与原始时长的对应关系。
- 复核过的疑点窗口和恢复范围。
- 确认删除的解码伪影及证据。
- 仍无法消除的不确定性。
```

## Final quality gates

- Every source recording has a corrected deliverable.
- Source media hashes match the pre-processing values.
- Timestamps are monotonic and reach the last audible speech.
- Speaker labels are descriptive and evidence-based.
- Repetition counts exclude decoder loops and preserve interruptions.
- Intent-based corrections do not erase deliberately wrong or random source material.
- Unverified claims are attributed, not endorsed.
- Low-confidence text is visibly marked.
- Model, settings, diarization method, and external-service usage are disclosed.
- Temporary artifacts are removed unless explicitly requested.
