# 新手操作指南素材

四段由 HyperFrames 渲染的操作指南，嵌入根目录 README；用户可在 [逐步图解](../../docs/visual-guide.md) 查看静态步骤或打开可暂停的视频。

| 文件前缀 | 内容 | 交付 |
| --- | --- | --- |
| `01-install` | 复制安装提示词、发给 Agent、记下扩展目录 | GIF、MP4、3 张静态图 |
| `02-extension` | 扩展管理页、开发者模式、加载文件夹 | GIF、MP4、3 张静态图 |
| `03-save` | 打开收藏、开始读取、查看进度与暂停 | GIF、MP4、3 张静态图 |
| `04-ask` | 新建聊天、提问、查看出处与追问 | GIF、MP4、3 张静态图 |

GIF 为 960 × 600、10 fps、23.4 秒循环；MP4 为 1280 × 800、10 fps，可暂停。静态图来自最终渲染视频。每一步留出阅读时间，无配音；这些是操作说明，不是平台采集速度演示。

## 来源与边界

- `hyperframes/index.html` 是可编辑的 HyperFrames HTML / GSAP 时间轴，根项目由 CLI init 建立；`DESIGN.md` 定义品牌与动效规则。
- `panels/*.png` 是当前扩展真实 HTML 的合成状态截图，`panels/metadata.json` 记录界面文案、按钮位置和源码哈希；`capture_panels.py` 可以复现，屏蔽远程请求。
- 浏览器、目录窗口和 Agent 聊天为简化示意，始终标明示例性质；不包含实际账号、收藏、安装结果或模型回答。
- 字体沿用本项目 MiSans，许可随 `hyperframes/shared/MiSans-license.pdf` 保存。GSAP 3.14.2 原发行文件保留版权头，发行说明与许可入口见 `hyperframes/shared/GSAP-README.md`。

## 重建

本次使用 HyperFrames **0.9.93**、GSAP **3.14.2**、FFmpeg **6.1.1**。项目根目录中可准备独立工具目录，避免改动应用依赖：

```powershell
npm install --prefix .tmp/hyperframes-tools --no-audit --no-fund hyperframes@0.9.93 gsap@3.14.2 ffmpeg-static@5.3.0 ffprobe-static@3.1.0
.\.venv\Scripts\python.exe assets/guides/capture_panels.py
.\.venv\Scripts\python.exe assets/guides/build_composition.py
```

设置当前进程的工具位置，使用 CLI 自动发现的隔离 Chrome；机器未准备浏览器时按 `doctor` 提示安装：

```powershell
$env:HYPERFRAMES_FFMPEG_PATH = (Resolve-Path .tmp/hyperframes-tools/node_modules/ffmpeg-static/ffmpeg.exe).Path
$env:HYPERFRAMES_FFPROBE_PATH = (Resolve-Path .tmp/hyperframes-tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe).Path
$guideCli = '.tmp/hyperframes-tools/node_modules/hyperframes/bin/hyperframes.mjs'
node $guideCli check assets/guides/hyperframes
node $guideCli snapshot assets/guides/hyperframes --at 4,12,20,28,36,44,52,60,68,76,84,92 --output var/guide-review
node $guideCli render assets/guides/hyperframes --output var/guides-master.mp4 --fps 10 --quality high --workers 1
.\.venv\Scripts\python.exe assets/guides/export_guides.py --ffmpeg $env:HYPERFRAMES_FFMPEG_PATH
```

`segments.json` 记录分段与静态图时间点。每段导出时跳过开场 0.6 秒，让 GIF 第一帧直接显示可读标题。依赖、渲染日志、缓存和完整母版留在 `.tmp/`、`var/` 或被忽略的 `.hyperframes/` 中，不随源码包发布。

## 本次检查

2026-11-25：HyperFrames 综合检查通过，运行、布局和对比度检查均无错误。已查看十二个关键场景，并核对最终视频导出的静态图。四个 GIF 均为 234 帧、23.4 秒，循环开启；README 与逐步图解的媒体路径已检查。

保留的工具提示包括：同一标志重复使用、十二场景在同一时间轴、建议拆子合成、开场动画首帧为空及没有音频。重复标志和单时间轴为编辑选择；交付 GIF 已跳过空开场，无声符合 GIF 用途。字体超过工具自动内联上限，随本地源项目保存，实际渲染已成功加载。

技能附带的旧版 animation-map 脚本依赖当前 CLI 未提供的 producer API，未生成报告；当前 CLI 的 motion 专项检查也未启用。动画采用最终视频关键帧与 GIF 帧数、时长核对，不宣称通过独立自动动效审计。

本次未安装用户组件、使用真实收藏或访问小红书，不构成真实采集验收。
