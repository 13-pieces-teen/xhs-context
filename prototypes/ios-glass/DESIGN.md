# 收藏上下文 · iOS 风格交互设计稿

本设计方向已经用户确认并用于当前生产插件；实际接入规格见 [面板设计文档](../../docs/panel-design.md)。本目录保留独立的 360 × 600px 合成交互演示，入口为 [index.html](index.html)。截至 **2026-11-24** 核验，Apple 官方列出的最新正式版为 **iOS 27.2.1**，发布于 2026-11-09；设计依据采用该版本所属的 Liquid Glass 体系与现行 HIG。[版本依据](https://support.apple.com/en-us/100100)

## 设计取舍

- **内容与操作分层。** 双圆环、计数、任务说明使用实底卡片；刷新、浮动工具栏与临时对话面板使用玻璃。Apple 将 Liquid Glass 定义为控制和导航的独立层，要求避免用它铺满内容层；文字较多的面板偏向较浓的 regular 材质。[Materials](https://developer.apple.com/design/human-interface-guidelines/materials)
- **圆角连续，文字有序。** 容器与内部控件采用逐级收小的圆角；浅灰底、白色内容面、陶土红主操作形成层级。Apple 建议嵌套形状保持同心关系，并用字号、字重和颜色区分信息。[布局与控件](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)、[Typography](https://developer.apple.com/design/human-interface-guidelines/typography)
- **突出当前操作。** 每种任务状态突出一个主操作，次要操作使用两列按钮；按下时有即时反馈。此稿按桌面 CSS 像素适配窄面板，不声称所有控件满足 iOS 原生 44pt 命中区标准。[Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons)
- **辅助功能按需展开。** 读取详情、设置、帮助、图片任务和正文补全用单层 sheet，保留关闭、Esc、焦点约束与关闭后焦点返回。没有多层面板叠加，也没有暗示可拖动的装饰把手。[Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets)

## 本稿视觉参数

| 项目 | 参数 |
| --- | --- |
| 插件画布 | 360 × 600px；内容纵向滚动；浮动工具栏上方预留空间 |
| 圆角 | 外框 28px；内容卡片 22px；主按钮 23px；工具栏 26px |
| 字号 | 主标题 22px；正文与操作 11–14px；状态和辅助说明 10–11px |
| 浅色 | 背景 `#F5F5F7`；内容 `#FFFFFF`；文字 `#242529`；强调 `#A64733` |
| 深色 | 背景 `#19191C`；内容 `#26262B`；文字 `#F1F1F3`；强调 `#EFB29D` |
| 字体 | Apple 平台优先系统字体；Windows 回退到项目已有 MiSans；不下载或打包 SF 字体 |
| 玻璃 | 演示浓度 40–100%，初始 72%；多文字 sheet 保持更高不透明度以确保可读性 |

浅色、深色和浓度滑块用于评估同一方向。iOS 27 官方说明包含从清透到浓色的材质调节；本稿的百分比、颜色、模糊值与圆角均为 Web 适配参数，不是系统参数的测量值。[iOS 27 设计更新](https://www.apple.com/newsroom/2026/09/major-updates-for-apples-software-platforms-are-now-available/)

## 动态与交互

| 反馈 | 本稿拟定的 Web 参数 |
| --- | --- |
| 按下 | 120ms；轻微缩放到 0.97；松开恢复 |
| 状态提示 | 180ms；短距离位移与淡入 |
| Sheet 展开 | 260ms；`cubic-bezier(.2,.85,.25,1)` |
| Sheet 退出 | 200ms；`cubic-bezier(.4,0,1,1)` |

动画支持中断；进度播放只由用户触发。系统 `prefers-reduced-motion` 或演示开关启用时，取消位移、缩放与过渡。依据是简短、精确、可取消的反馈，以及减少动态效果时避免反复运动与模糊变化；上述时长和曲线是**本稿拟定**，不是 iOS 原生动效精确复刻，也没有真实设备流畅度或帧率证明。[Motion](https://developer.apple.com/design/human-interface-guidelines/motion)、[Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)

## 可演示范围

包含待开始、已暂停、读取中、访问受限、总数未知、已结束、核对末尾、连接中断共 8 种状态。设置只在待开始时可编辑；图片任务独立展示与暂停。列表达到 100% 仍需核对末尾，未知总数不显示虚构百分比，跳过失效笔记不计作正文保存。

本目录的全部计数与操作仍为合成演示，不调用扩展 API、不连接真实资料库、不启动采集。生产插件已经采用这一设计方向，并接回真实任务状态与原有命令；本目录作为交互参考保留，不是插件运行时依赖。

复用项目内固定版本 **daisyUI 5.8.2** 的 Button、Radial progress、Input、Checkbox、Collapse；图标使用本地 **Lucide 0.468.0** SVG。图标来源、哈希及许可证保存在 [icons](icons/)；组件许可证沿用 [daisyUI vendor](../../browser-extension/vendor/daisyui/README.md)。无需新增运行时框架或联网加载资源。

## 查看与验证

在浏览器直接打开本目录的 `index.html`。右侧可切换状态、浅深色、玻璃浓度和减少动态效果；点击插件内的操作可体验反馈与面板展开。

在项目根目录运行隔离检查：

```powershell
.venv\Scripts\python.exe prototypes\ios-glass\validate.py
```

截图和验证报告输出到 `var/ios-glass/`。检查覆盖合成状态的布局、键盘、焦点、面板切换、表单及减少动态效果；自动检查结果不等同于真实 iOS 设备验收。
