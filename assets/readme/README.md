# README 展示资源

沿用「文脉」品牌的陶土红、深陶土与纸张色，不替换现有标志。

| 文件 | 来源与用途 |
| --- | --- |
| `hero-overview.png` | 内置 image_gen 生成的概念插画，说明本地资料库、CLI、Skill 与 Agent 的配合；不是界面截图，不含真实收藏 |
| `generation.json` | 首图的完整生成提示词、工具与日期 |
| `panel-overview.png` | 当前扩展 HTML 的实际渲染，来自 `scripts/panel_smoke.py` 的合成状态；不是 AI 生成界面，也不是线上采集结果 |

生成日期：2026-11-22。概念插画中的文字仅作层次标识；准确分工以 README 的表格、流程图与 Skill 为准。图片可随源码分发，真实资料和个人测试输出仍保留在本机。

更新界面预览时，在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/panel_smoke.py
Copy-Item -LiteralPath var/panel-design/overview.png -Destination assets/readme/panel-overview.png
```

检查截图内容与合成数据标识后再更新 README 资源。不复制真实收藏截图、Cookie、账号资料或本机安装报告到这里。
