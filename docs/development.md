# 开发与验证

[返回 README](../README.md) · [使用指南](usage.md) · [Agent 安装指南](agent-install.md)

本文整理源码开发、自动化检查和打包入口。日常安装与查询请使用上面的指南。

## 准备环境

在项目根目录打开 PowerShell，准备 Python 3.11–3.13、uv 和 Node.js。当前 [Windows CI](../.github/workflows/tests.yml) 使用 Python 3.12、Node.js 22；具体依赖以 [pyproject.toml](../pyproject.toml) 和 [uv.lock](../uv.lock) 为准。

```powershell
uv sync --frozen --group dev
```

这一步安装项目和开发依赖。需要验证浏览器界面或扩展链路时，再安装 Playwright Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Python 与扩展测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest
node --test browser-extension/tests/*.test.mjs
```

Python 测试覆盖 CLI、存储、检索、版本、任务状态和本机组件等行为；JavaScript 测试覆盖扩展的页面读取、队列、恢复与停止逻辑。测试使用隔离配置和合成资料，不以个人收藏作为默认测试库。

## 浏览器检查

按修改涉及的范围运行对应脚本。以下命令也收录在 CI 中：

```powershell
# 页面诊断与图片区域适配
.\.venv\Scripts\python.exe scripts/source_unavailable_dom_smoke.py
.\.venv\Scripts\python.exe scripts/gallery_dom_smoke.py

# 扩展与 Native Messaging 的正文、图片链路
.\.venv\Scripts\python.exe scripts/browser_smoke.py
.\.venv\Scripts\python.exe scripts/images_smoke.py

# 面板交互、布局与浏览器弹窗尺寸
.\.venv\Scripts\python.exe scripts/panel_smoke.py
.\.venv\Scripts\python.exe scripts/popup_window_smoke.py
```

浏览器检查使用隔离 Chromium、测试扩展和合成页面，不操作个人浏览器中的收藏。Native Messaging 链路检查会注册独立的测试宿主，并在结束时清理测试注册项。报告与截图保存在本地 `var/` 下，例如 `var/validation`、`var/panel-design` 和 `var/popup-width`。

阅读报告时保留 `NOT_RUN`、跳过项和失败原因。合成页面通过只证明对应测试场景中的行为，不代表真实站点适配、全量收藏覆盖或持续采集已经验收；最新检查范围见 [验证记录](validation.md)。

## 源码打包

```powershell
.\.venv\Scripts\python.exe scripts/package.py
```

脚本从扩展 [manifest.json](../browser-extension/manifest.json) 读取版本，在 `dist/` 生成 `xhs-context-prototype-<版本>.zip` 和对应 `.sha256` 校验文件。这是源码包，使用者仍需安装依赖和本机组件；打包成功不代表已发布，也不代表完成了真实安装验收。

打包范围包含源码、扩展、Skill、脚本、合成示例、测试、文档与品牌资源。真实收藏、个人测试输出和本机配置应保留在本地，不放入这些源码目录或分发包。

## 文档与维护入口

| 资料 | 内容 |
| --- | --- |
| [Agent 安装指南](agent-install.md) | 安装脚本、结构化报告、升级与安装验收边界 |
| [使用指南](usage.md) | 搜索、原文回读、图片、分页与缺失处理 |
| [CLI 协议](protocol.md) | 请求、响应、游标、错误、本机接口、备份与恢复 |
| [Skill](../skills/xhs-context/SKILL.md) | Agent 的调用方式、证据要求和后续判断规范 |
| [验证记录](validation.md) | 每轮检查的证据、通过范围与未验证项 |
| [更新记录](../CHANGELOG.md) | 已发布版本与工作区未发布变更 |
| [CI 工作流](../.github/workflows/tests.yml) | 自动化环境与执行步骤 |
| [问题样例](../examples/evaluation-questions.json) | 收藏查询的任务示例 |
| [项目计划书](../小红书收藏上下文项目计划书.md) | 项目背景与设计依据 |
| [面板说明](panel-design.md) | 扩展面板的交互与布局记录 |
| [品牌资源](../assets/brand/README.md) | 文脉标志、字标、颜色与导出方式 |
| [展示图说明](../assets/readme/README.md) | README 概念插画、合成界面预览的来源与更新方式 |

CLI 负责可靠地提供原文、分页、版本和缺失状态；Skill 说明使用方式与证据要求；Agent 负责问题拆解、选读和后续判断。修改功能时同步更新对应协议、Skill 和使用说明，验证结论只覆盖实际执行过的场景。
