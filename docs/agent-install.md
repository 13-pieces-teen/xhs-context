# 让 Agent 安装 xhs-context

安装目标是让 Agent 能按 Skill 调用本地 CLI、读取已保存的收藏。**CLI 负责原文、分页、版本和缺失状态；Skill 说明使用方式与证据要求；Agent 负责问题拆解、选读和后续判断。**

当前自动安装支持 **Windows + Codex**，采集端使用 **Chrome / Edge**。需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.11–3.13；uv 会按项目声明选择兼容的 Python。用 Git 获取源码时还需要 Git。安装依赖会联网，安装过程不读取小红书、不启动采集、不下载 OCR 模型，也不需要配置模型 API key。

## 复制给 Agent

```text
请帮我安装 xhs-context，让你能查询我已保存的小红书收藏。
项目：https://github.com/13-pieces-teen/xhs-context

1. 先确认当前系统、Agent 宿主和已有安装。读取项目 README.md、
   docs/agent-install.md 与 skills/xhs-context/SKILL.md。
   目前自动安装支持 Windows + Codex；其他宿主不要假定已经适配。
2. 优先复用已有项目目录和 %LOCALAPPDATA%\xhs-context\config.json。
   没有源码时，把仓库克隆到可长期保留的目录。检查 uv 是否可用，
   Python 需满足 3.11–3.13；缺少 uv 时按官方说明协助安装。
3. 在项目目录运行：
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-agent.ps1
   读取 stdout JSON 和退出码；失败时根据 stderr 与 error 修复。
   不把安装目录放在临时目录，不覆盖其他安装拥有的 native host 注册。
4. 退出码为 0、ok=true、state=local_cli_ready，只表示本地 CLI 和 Skill
   启动器检查通过。报告实际 library、skill、extension_dir 与限制状态。
   不把空资料库、浏览器未连接或新聊天尚未加载 Skill 说成完整可用。
5. 如果我要采集，指导我在 Chrome/Edge 中加载返回的 extension_dir。
   指导我开启新的 Codex 聊天加载 Skill，并用 status 核对同一个资料库。
   已有收藏时，按我的问题做一次搜索并回读固定版本，保留来源与缺口。
6. 安装与查询不构成采集授权。不要自行启动同步、补图、解锁限制，
   不把 var/demo 或测试夹具当成我的收藏。遇到访问限制只说明状态。
```

这段提示词的安装入口来自当前源码。使用旧源码包时先检查脚本是否存在；旧版安装包不会自动获得尚未发布的更改。

## 命令行安装

首次获取源码，在准备长期存放项目的父目录执行：

```powershell
git clone https://github.com/13-pieces-teen/xhs-context.git
cd xhs-context
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-agent.ps1
```

已有项目直接运行最后一条；升级优先在原目录更新源码后重跑。安装会创建项目 `.venv`、登记当前 Windows 用户的 Chrome / Edge Native Messaging 组件、初始化或沿用资料库，并安装 Codex Skill。它不修改浏览器配置文件。项目目录被移动或删除后，配置中的 CLI 路径会失效。

默认 Skill 目录是 `$CODEX_HOME/skills/xhs-context`；没有 `CODEX_HOME` 时使用 `%USERPROFILE%/.codex/skills/xhs-context`。已有 Skill 会先备份到项目的 `var/backups`。默认保留配置中的资料库，首次安装通常使用 `%LOCALAPPDATA%/xhs-context/library`；最终路径以返回值为准。

需要指定路径时：

```powershell
# 显式选择资料库；不会搬运原库内容。
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-agent.ps1 -Library "D:\MyArchive\xhs"

# 安装到自己指定的 Skill 目录。
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-agent.ps1 -SkillDestination "D:\MyAgent\skills\xhs-context"
```

`-Library` 是选择库，不是迁移命令。若 `XHS_CONTEXT_HOME` 与所选库不一致，入口会在安装前报错，避免 Agent 和浏览器意外使用不同资料库。先核对该环境变量的用途，再修正配置。其他 Agent 宿主需支持本地命令执行、读取 Skill，查看图片还需本地图片工具；自定义目录仅负责复制文件，不代表该宿主已集成或验收。不需要新增 MCP server 配置。

## 机器验收结果

入口非交互运行；**stdout 是一个 JSON 对象，安装日志在 stderr**。成功退出码为 `0`，失败为 `1`。

下面是结构示例，路径和数量仅用于说明：

```json
{
  "schema_version": 1,
  "ok": true,
  "check_only": false,
  "state": "local_cli_ready",
  "cli": "D:\\Projects\\xhs-context\\.venv\\Scripts\\xhs-context.exe",
  "version": "0.5.1a1",
  "library": "C:\\Users\\example\\AppData\\Local\\xhs-context\\library",
  "skill": "C:\\Users\\example\\.codex\\skills\\xhs-context\\SKILL.md",
  "extension_dir": "D:\\Projects\\xhs-context\\browser-extension",
  "checks": {"doctor": "passed", "skill_launcher": "passed", "configured_library": "passed"},
  "notes": 0,
  "access_restriction": null,
  "browser_connection": "not_verified",
  "agent_skill_loading": "not_verified",
  "next_steps": ["Load or reload browser-extension in Chrome/Edge if collection is needed."]
}
```

| 检查项 | 实际证明什么 |
| --- | --- |
| `doctor=passed` | CLI 能启动，SQLite FTS5 可用，所选资料库已初始化 |
| `skill_launcher=passed` | 从另一个工作目录运行已安装 Skill 的启动器，`status` 成功 |
| `configured_library=passed` | Skill 实际读取的库与安装配置一致 |
| `notes` / `access_restriction` | 已归档笔记数与当前限制状态；数量不代表正文、图片全齐 |
| `browser_connection=not_verified` | 脚本没有打开浏览器核实扩展连接 |
| `agent_skill_loading=not_verified` | 启动器可用，不代表新聊天已经发现并加载 Skill |

`access_restriction` 透传 CLI 当前状态；即使访问受限，本地验收也可以成功。验收不修改该状态。

只检查已有安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-agent.ps1 -CheckOnly
```

使用自定义 Skill 目录时同时传 `-SkillDestination`。`-CheckOnly` 仅运行 `doctor` 和通过 Skill 启动器运行的 `status`，不安装依赖、不注册浏览器组件、不初始化新库、不搜索或输出收藏正文。它不接受 `-Library`，避免绕过对已配置资料库的检查。

## 浏览器与新聊天

1. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`，启用开发者模式，选择“加载已解压的扩展程序”，加载返回的 `extension_dir`。已有扩展点击“重新加载”。
2. 需要采集时，在登录的小红书页面打开扩展，确认本机连接；再由用户发起“读取当前收藏夹”。浏览器操作、登录验证和采集完成是独立阶段。
3. 开启新 Codex 聊天，让 Agent 读取已安装 Skill 并调用 `status`。如果没有自动发现，可明确指定返回的 Skill 完整路径。
4. 已有资料时，提出一个真实问题，由 Agent `search → read → 判断`。只搜索到标题不能算读过正文；图片缺失也应保留在结论中。空库应先说明没有已归档资料。

已有资料的离线查询不需要打开浏览器，也不要求启动服务。完整采集说明见 [使用指南](usage.md)。

## 常见安装问题

| 现象 | 处理方式 |
| --- | --- |
| 找不到 `uv` | 按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装后重新打开终端 |
| `HOST_ALREADY_REGISTERED` | 另一个安装拥有组件；回到原安装目录升级，不自行删除或覆盖注册信息 |
| 配置的 CLI 不存在 | 项目可能被移动；先找回原目录，再修复安装路径 |
| `XHS_CONTEXT_HOME` 冲突 | 确认应使用哪个库，修正覆盖变量后再运行；脚本不会替用户搬库 |
| 本地检查通过但扩展未连接 | 检查是否加载返回的扩展目录，重新加载扩展并查看其连接状态 |
| Skill 已安装但 Agent 未发现 | 新建聊天，或用完整路径指定 `SKILL.md`；仅能执行 CLI 的宿主仍需正确加载使用要求 |
| `300013` / HTTP `429` | 保持采集停止，继续查询本地已保存资料；重新安装不能解除平台限制 |

## 验证范围

安装入口的自动测试使用隔离的配置、资料库和 Skill 目录，调用真实 CLI 与跨目录启动器；安装编排测试以替身替换会注册本机组件的步骤。覆盖 JSON 输出、错误退出、旧库保留、环境变量冲突和缺失配置，不会修改个人注册表或启动线上采集。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_install.py
```

这些检查不替代真实首次安装、浏览器加载或新聊天自动发现 Skill 的验收。
