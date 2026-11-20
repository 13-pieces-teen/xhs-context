# xhs-context 品牌资源 · 文脉

2026-11-20，用户选择候选 C「文脉」，作为整个项目的正式标志。交错纸带表达把分散收藏连接为可检索、可引用的上下文；项目名称仍为 xhs-context，中文名称仍为“收藏上下文”。

## 文件与使用

| 资源 | 用途 |
| --- | --- |
| `xhs-context-mark.png` | 经内置 image_gen 清理的透明 PNG 原图；保留所选纸带轮廓和双层关系 |
| `xhs-context-mark-256.png` / `xhs-context-mark-512.png` | 项目头像、文档及界面中的独立图标 |
| `xhs-context-wordmark-light.png` | 浅色背景的图标与 MiSans 字标组合 |
| `xhs-context-wordmark-dark.png` | 深色背景的图标与 MiSans 字标组合 |
| `../../browser-extension/icons/icon-*.png` | 扩展工具栏和管理页使用的 16 / 20 / 24 / 32 / 48 / 64 / 128px 图标 |
| `preview.html` | 品牌应用预览，含实际面板的合成状态截图 |

显示独立图标时保留原画布留白，不额外套红色底板。浅色字标的文字为 `#352A27`，深色字标的文字为 `#FCF7F5`；MiSans Semibold（600）。界面陶土红为 `#AD4C38`、深陶土为 `#913C2B`；位图折面仍保留生成结果的少量明暗，不宣称每个不透明像素严格等于品牌色。当前交付是 PNG，未伪装为可编辑矢量文件。

图标原始生成与清理过程见 [generation.json](generation.json)。原候选保留在 `output/logo-candidates-2026-11-20`。

## 接入位置

- `browser-extension/popup.html`：36px 标志替代旧 `xhs` 字母色块；页面 favicon 同步更新。
- `browser-extension/manifest.json`：`icons` 与 `action.default_icon` 指向本地 PNG 图标。
- 根目录 `README.md`：通过 `<picture>` 提供浅色、深色字标。
- `scripts/package.py`：从扩展 manifest 读取版本并包含 `assets`，同时生成 SHA-256 校验文件。

图标使用 PNG 与多尺寸导出，依据 [Chrome manifest 图标文档](https://developer.chrome.com/docs/extensions/reference/manifest/icons) 和 [action 图标文档](https://developer.chrome.com/docs/extensions/reference/api/action)。

## 重建

在项目根目录运行 `./scripts/export-brand-icons.ps1`。此步骤只按尺寸导出已选原图，不改动设计。字标通过 `wordmark.html` 加载项目内置 MiSans，并以透明背景导出为 880 × 240px PNG；`?theme=dark` 使用浅色文字。字体许可位于 `browser-extension/fonts/MiSans-license.pdf`。

个人浏览器中的扩展需要重新加载后才会显示更新。Figma 初版仍受 MCP 额度限制，本次未同步。
