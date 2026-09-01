# 抖音公开视频下载实现与排障

仅在主技能已经确认用户授权、视频可在当前页面公开播放、Edge `Profile 1` 和输出目录后读取本参考。

## 1. 只读核对 Edge profile

Edge 窗口中的“个人 2”等名称是 UI 显示名，不能据此推断 profile 目录。可在 PowerShell 中只读核对主进程：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" |
  Where-Object { $_.CommandLine -match '--profile-directory' } |
  Select-Object ProcessId, CommandLine
```

继续执行所需证据是命令行含 `--profile-directory="Profile 1"`。不得读取 Edge 用户数据目录、cookies、Local Storage 或 session 文件。

## 2. 精确锁定页面和播放器

按 `$chrome:control-chrome` 建立或复用 Edge binding，并使用扩展返回的目标 tab。维护 `https://www.douyin.com/` allowlist，记录：

- 当前 tab URL 和页面标题；
- URL 中的 `/video/<aweme_id>` 或 `modal_id=<aweme_id>`；
- 页面显示的作者和文案；
- 可见主播放器的时长、暂停状态、尺寸及媒体源。

抖音页面可能预加载多个播放器，甚至多个视频同时具有全屏尺寸或可见矩形。优先用 URL 的 `modal_id`、`/video/<aweme_id>` 或播放器容器 `data-e2e-vid` 锁定目标；只有没有 ID 线索时，才退回到当前可见、面积最大并与作者/标题/时长一致的主播放器。不要使用 `document.querySelector('video')` 后未经核对直接下载。下面的页面侧示例用于生成候选信息，仍需把结果与 URL、作者、标题和页面时长交叉核对：

```js
const media = await tab.playwright.evaluate(() => {
  const videos = [...document.querySelectorAll("video")];
  const ranked = videos.map((video) => {
    const rect = video.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 &&
      rect.bottom > 0 && rect.right > 0 &&
      rect.top < innerHeight && rect.left < innerWidth;
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    return { video, score: (visible ? 1e12 : 0) + (!video.paused ? 1e10 : 0) + area };
  }).sort((a, b) => b.score - a.score);

  const video = ranked[0]?.video;
  if (!video) return null;

  const sources = [
    video.currentSrc,
    video.src,
    ...[...video.querySelectorAll("source")].map((source) => source.src),
  ].filter((url, index, all) =>
    /^https?:\/\//i.test(url) && all.indexOf(url) === index
  );

  const rect = video.getBoundingClientRect();
  return {
    pageUrl: location.href,
    containerVideoId: video.closest("[data-e2e-vid]")?.getAttribute("data-e2e-vid") ?? null,
    duration: Number.isFinite(video.duration) ? video.duration : null,
    paused: video.paused,
    width: video.videoWidth,
    height: video.videoHeight,
    rect: { width: rect.width, height: rect.height },
    sources,
  };
});
```

`currentSrc` 为 `blob:` 并不表示没有可下载源；继续检查同一播放器下的 `<source src>`。只有 `https:` 候选可进入本流程。若主播放器的 `currentSrc`、`src` 和所有 `<source>` 都只有 `blob:` 或为空，这和“页面已经缓存并可播放”不是一回事；没有可在主机侧普通 GET 的公开媒体地址时应停止。

若 URL 是 `https://www.douyin.com/user/self?...&modal_id=<aweme_id>`，可把 `<aweme_id>` 规范化为 `https://www.douyin.com/video/<aweme_id>`。这不只是记录格式：主页弹窗可能只保留 `blob:`，而规范视频页在同一已登录 Edge 会话中重新加载后，DOM 可能恢复 `<source>` 或 `currentSrc` 中的 `https:` 签名媒体源。因此在判定 blob-only 失败前，应该先用当前受控 Edge tab 打开规范页、等待播放器加载、重新核对作者/标题/时长/ID，再提取媒体源。

下载器通常不支持 `user/self?...modal_id=...` 形式。下载器对 `/video/<aweme_id>` 返回 “Fresh cookies are needed” 时，说明该路径需要浏览器凭据；默认边界内不得使用 `--cookies-from-browser` 或读取 profile/cookies。只有用户在本轮明确授权使用登录态时，才可尝试受信下载器的 cookies-from-browser 路径，并且不得打印或保存明文 cookies。若 Windows 返回 cookie 数据库 `PermissionError`，不要误判为未登录；这可能是文件权限、锁定或沙箱用户组导致。优先回到“规范页刷新后提取 `https:` 媒体源”的路径。

若扩展提供 `pageAssets`，可以只读查看已观测资源清单。`kind=video` 为 0 是强证据：当前扩展没有观测到可直接导出的媒体资源。清单中的 `/aweme/v1/web/aweme/detail/`、`iteminfo` 或类似 XHR 也必须核对 `aweme_id`，因为抖音会保留相邻/历史预加载视频的数据；ID 不等于目标视频时不得拿来解析媒体地址。

## 3. 安全文件名与落盘契约

使用作者、短标题和视频 ID 生成名称。Windows 文件名必须移除控制字符和 `< > : " / \\ | ? *`，去掉末尾的点和空格，并限制主体长度。视频 ID 放在末尾，避免标题相同导致混淆。

下载阶段遵循：

1. 最终文件不存在，或已有文件已经通过相同视频 ID 和媒体验收；
2. 本次下载使用唯一 `.part-<uuid>`，不得复用未知来源的 `.part`；
3. 使用流式写入，避免把长视频整体读入内存；
4. 成功响应、流完整结束且验收通过后才改名；
5. 失败时只删除本次创建的精确 partial 文件。

主机侧 Node.js 下载范式如下。`outputDir`、`safeFileName`、`mediaUrl`、`canonicalVideoUrl`、`browserUserAgent`、`expectedDuration` 和 `expectedAudio` 必须先由已核对的当前页面与输出契约赋值：

```js
const fs = await import("node:fs");
const fsp = await import("node:fs/promises");
const path = await import("node:path");
const crypto = await import("node:crypto");
const { execFile } = await import("node:child_process");
const { Readable } = await import("node:stream");
const { pipeline } = await import("node:stream/promises");
const { promisify } = await import("node:util");

const runFile = promisify(execFile);

const finalPath = path.join(outputDir, safeFileName + ".mp4");
const partPath = finalPath + ".part-" + crypto.randomUUID();

if (await fsp.stat(finalPath).then(() => true, () => false)) {
  throw new Error("目标文件已存在；先校验复用或选择新文件名，禁止覆盖");
}

try {
  const response = await fetch(mediaUrl, {
    redirect: "follow",
    headers: {
      "User-Agent": browserUserAgent,
      "Referer": canonicalVideoUrl,
      "Accept": "*/*",
    },
  });
  if (!response.ok || !response.body) {
    throw new Error(`媒体请求失败: HTTP ${response.status}`);
  }
  await pipeline(
    Readable.fromWeb(response.body),
    fs.createWriteStream(partPath, { flags: "wx" }),
  );

  const { stdout } = await runFile("ffprobe", [
    "-v", "error",
    "-show_entries",
    "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height",
    "-of", "json",
    partPath,
  ], { windowsHide: true });
  const probe = JSON.parse(stdout);
  const duration = Number(probe.format?.duration);
  const size = Number(probe.format?.size);
  const hasVideo = probe.streams?.some((stream) =>
    stream.codec_type === "video" && stream.width > 0 && stream.height > 0
  );
  const hasAudio = probe.streams?.some((stream) => stream.codec_type === "audio");
  const tolerance = Math.max(2, expectedDuration * 0.01);
  if (!hasVideo || size <= 0 || !Number.isFinite(duration)) {
    throw new Error("ffprobe 验收失败：容器、视频流、尺寸或时长异常");
  }
  if (expectedAudio && !hasAudio) {
    throw new Error("ffprobe 验收失败：缺少预期音频流");
  }
  if (Number.isFinite(expectedDuration) &&
      Math.abs(duration - expectedDuration) > tolerance) {
    throw new Error("ffprobe 验收失败：下载时长与页面时长不符");
  }

  await fsp.rename(partPath, finalPath);
} catch (error) {
  await fsp.rm(partPath, { force: true });
  throw error;
}
```

候选 URL 逐个尝试，但重试次数必须有上限。日志只记录候选序号、媒体域名、HTTP 状态和字节数，不记录完整签名查询串。

## 4. 验收

先对 partial 文件执行：

```powershell
ffprobe -v error `
  -show_entries "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height" `
  -of json -- "<absolute-part-path>"
```

通过条件：

- `ffprobe` 退出码为 0，容器可解析；
- 至少一条视频流；页面是有声视频时至少一条音频流；
- 宽高均为正数，文件大小大于零；
- 下载时长与页面播放器时长之差不超过 `max(2 秒, 页面时长 × 1%)`；
- 最终改名后再次确认文件存在且大小未变化。

需要归档证据时可额外执行：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath "<absolute-final-path>"
```

HTTP 200、非空文件、下载 API 未抛错或工具返回 `undefined` 都不能替代以上验收。

## 5. 本次实战问题与处理办法

| 现象 | 判断 | 处理 |
|---|---|---|
| Edge UI 显示“个人 2”，怀疑不是 Profile 1 | UI 名称与底层目录名不同 | 只读检查 Edge 主进程 `--profile-directory`；以 `Profile 1` 为准 |
| 官方按钮提示“该视频不支持下载” | 官方下载入口关闭，不等于公开播放器没有媒体源 | 视频仍可公开播放时，读取主播放器的 `currentSrc`、`src` 和 `<source src>` |
| 主页弹窗 `user/self?...modal_id=...` 只有 `blob:` | 弹窗/个人主页状态可能没有保留可普通 GET 的媒体源 | 在当前已登录 Edge tab 打开 `https://www.douyin.com/video/<id>`，等待加载后重新核对目标并读取 `<source>`；成功时用新暴露的 `https:` 源下载 |
| `currentSrc` 是 `blob:`，但 `<source>` 保留 `https:` | 播放器使用对象 URL，同时 DOM 仍暴露公开签名媒体源 | 不下载 blob；改用同一 `<video>` 的签名 `https:` 地址 |
| `currentSrc`、`src` 和 `<source>` 全部只有 `blob:` 或为空 | 当前页面没有暴露可主机侧普通 GET 的公开媒体地址 | 停止并报告 blob-only；不要把浏览器可播放、缓存可播或扩展调用完成当作下载成功 |
| 多个视频都有全屏可见矩形 | 抖音 swiper/推荐流会同时保留多个播放器 | 用 `modal_id`、`/video/<id>` 或 `[data-e2e-vid="<id>"] video` 精确锁定，不用第一个或面积最大的 `<video>` |
| 媒体下载方法返回成功或空值，但磁盘无文件 | 工具调用完成不等于交付物产生 | 以目标目录中的真实文件和 `ffprobe` 结果为准 |
| `pageAssets` 清单 `video=0` | 扩展没有观测到可直接导出的媒体文件 | 不能把资源清单里的脚本、图片、XHR 当视频；继续查 DOM `<source>`，仍无 `https:` 则停止 |
| `aweme/detail` 资源存在但 `aweme_id` 不是目标 | 当前页面残留了相邻或历史预加载视频请求 | 丢弃该响应，必须精确匹配目标 ID 后才允许解析 |
| 官方 detail/iteminfo 接口 HTTP 200 但响应长度为 0 | 表面成功、数据实际缺失 | 判失败，不继续解析空响应 |
| `yt-dlp` 不支持 `user/self?...modal_id=...` | 下载器不识别主页弹窗 URL | 规范化到 `/video/<aweme_id>` 只用于探测；不能因此改用 cookies |
| `yt-dlp` 对 `/video/<id>` 提示需要 fresh cookies | 该路径依赖浏览器凭据 | 默认不提取 cookies；若用户明确授权，可尝试受信下载器最小读取当前 Edge 登录态。更优先的低风险路径是规范页刷新后从 DOM 取 `https:` 媒体源 |
| `yt-dlp --cookies-from-browser edge:Profile 1` 报 cookie DB `PermissionError` | 下载器找到了正确 profile，但当前进程无法复制 Edge cookie DB | 不等于浏览器未登录；不要输出或手动解析 cookie。回到已登录 Edge 规范页刷新，提取页面暴露的 `https:` 源 |
| 签名媒体地址返回 403/404 | URL 过期或页面状态已变化 | 在目标页面重新读取 `<source>`，再次核对视频 ID 后有限重试 |
| 429、5xx、超时 | 瞬时限流或网络故障 | 退避重试少量次数；耗尽后保留证据并失败关闭 |
| 第三方解析接口空响应、限流或要求提交链接 | 可验证性和隐私边界较差 | 不依赖第三方解析站，回到当前页面的公开媒体源 |
| 页面含多个视频，可能抓到推荐内容 | 抖音会预加载相邻视频 | 用当前 URL、可见主播放器、作者、标题、时长四项交叉锁定 |

## 6. 完成报告模板

报告应包含：

- 下载对象：作者、标题、视频 ID、规范 URL；
- 文件：绝对路径、字节数、时长、分辨率、视频编码、音频编码；
- 验收：`ffprobe` 实际结果以及页面时长对比；
- 浏览器门槛：Edge `Profile 1` 已由 Codex Chrome Extension 接管；未使用 Google Chrome；未使用独立 Playwright/CDP/自建扩展；
- 限制或异常：发生过的失败路径、重试次数和是否留下 partial 文件。

最终只展示规范视频 URL 和本地文件链接；不要展示完整签名媒体 URL。
