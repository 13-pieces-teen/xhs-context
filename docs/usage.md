# 使用指南

[返回 README](../README.md) · [Agent Skill](../skills/xhs-context/SKILL.md) · [完整协议](protocol.md)

本指南面向当前源码。`images plan`、`read` 的可调 `context` 与摘录排序改进尚未包含在已有的 **0.5.1 prototype** 安装包中，详见 [版本记录](../CHANGELOG.md)。

## 先区分采集与查询

浏览器扩展把页面内容保存到本机；CLI 搜索和回读已经保存的资料；Skill 说明用法与证据要求；Agent 拆解问题、选读并决定下一步。查询不会自动打开小红书、补正文、下载图片或恢复采集。

在项目根目录运行以下命令可查看资料库路径、收藏范围、保存状态和访问限制：

```powershell
.\scripts\xhs.ps1 status
```

默认沿用安装配置中的资料库，通常为 `%LOCALAPPDATA%\xhs-context\library`，以返回的 `data.library` 为准。不要把 `var/demo` 当成自己的收藏。需要另一个库时，显式传入 `--library`；环境变量 `XHS_CONTEXT_HOME` 也可覆盖默认路径。

安装后的 Skill 自带跨目录入口，Agent 应以 Skill 所在目录为基准调用其 `scripts/xhs.ps1` 的绝对路径。下面的相对路径示例供项目根目录中的手动调用使用。

## 搜索：先找候选

```powershell
.\scripts\xhs.ps1 search --query "Madrid tapas" --query "马德里 美食" --require "马德里"
.\scripts\xhs.ps1 search --query "数据并行" --query "张量并行" --match phrase
.\scripts\xhs.ps1 search --query "infra" --content all
```

| 选项 | 用途 |
| --- | --- |
| `--query` | 1–4 个关键词查询，合并候选并按笔记去重 |
| `--require` | 每篇必须满足的条件，例如城市或技术名称；最多 4 个 |
| `--collection` | 按本地收藏夹 ID 限定范围，可重复；ID 从 `status` 获取 |
| `--match phrase` | 查证完整名词，要求查询连续出现在同一字段的一行中 |
| `--content all` | 同时查找只有标题、纯标签、正文缺失或既有 OCR 等线索 |
| `--limit` | 每页笔记数，1–50；不是全量结果上限 |
| `--max-chars` | 整个 JSON 响应的字符预算，512–200000；不是 token 数 |

默认 `content=body` 会在排序、分页前排除缺失正文、空白正文和只有标签或装饰符号的内容。短正文和“详细步骤见图”仍可返回；这项过滤不判断文章质量。默认 `match=terms` 允许关键词分散在同篇的不同段落；只含一半以上关键词的笔记排在所有完整命中之后，带 `partial_match` 列出缺少的词，`matched_count` 只计完整命中。`phrase` 更严格，不返回部分命中，也可能漏掉改写表达。

CLI 处理字面匹配和少量内置等价名称，如 Madrid / 马德里、Kubernetes / k8s。可以为本库补充等价名称，修改后已有搜索游标失效：

```powershell
.\scripts\xhs.ps1 aliases list
.\scripts\xhs.ps1 aliases add Tokyo 东京
.\scripts\xhs.ps1 aliases remove 东京
```

是否把“infra”进一步拆成容器、网络或可观测性，由 Agent 根据问题和原文决定，不应写成等价名称。

复杂请求可保存为 UTF-8 JSON 文件，例如 `search.json`：

```json
{
  "queries": ["Madrid tapas", "马德里 美食"],
  "required_terms": ["马德里"],
  "content": "body",
  "limit": 8,
  "max_chars": 6000
}
```

```powershell
.\scripts\xhs.ps1 search --input search.json
```

搜索结果在 `data.items`，包含固定的 `note_id`、`revision_id`、来源、命中字段和最多两段原文摘录。摘录的 `block_id` 与 `location` 可用于继续回读；`excerpt_only` 表示它只是段落的一部分。标题、目录或提问也可能成为摘录，第一条命中并不一定能回答问题。

## 回读：用固定版本核对原文

原链接已确认失效或来源已确认不可用时，搜索和回读都会在该篇的 `invalid_source` 中给出原因与首次确认时间。仍可按固定版本引用已有材料。字段缺省表示本库没有确认过来源失效。

从搜索结果复制 ID，保存为 `read.json`。下列大写 ID 都是占位符：

```json
{
  "items": [
    {
      "note_id": "NOTE_ID",
      "revision_id": "REVISION_ID",
      "block_ids": ["BLOCK_ID"]
    }
  ],
  "mode": "evidence",
  "context": 1,
  "max_chars": 12000
}
```

```powershell
.\scripts\xhs.ps1 read --input read.json
```

一次可读取 1–10 篇，每项必须指定 `note_id + revision_id`。选择读取范围：

| 模式 | 返回内容 | 适用场景 |
| --- | --- | --- |
| `evidence` | 指定段落与相邻上下文；正文在 `blocks[]` | 核对一句话、步骤或事实 |
| `full` | 该版本按原文顺序组织的全部证据块，可能分页 | 完整论述、跨段名单、上下文不足 |
| `asset` | 图片信息与本机 `path`；图片在 `assets[]` | 交给宿主的图像查看工具实际看图 |

`context` 仅用于 `evidence`，取值 0–4，表示每个选中块前后各返回多少块，默认 1。需要完整论述时使用 `full`，并去掉 `context`。未指定 `block_ids` 的 evidence 请求以开头最多三个块为锚点，不能替代 Agent 的选段。

同一笔记每页只出现一次标题、版本、来源和缺口，多个正文块或图片聚合在该篇条目内。引用时保留原文链接及 `revision_id/block_id`，并区分作者的描述和 Agent 的判断。后续采集产生新版本，旧版本引用仍可读取。

## 分页与部分结果

查看 `data.next_cursor`，有值说明本次请求还有未返回的内容。搜索续页：

```powershell
.\scripts\xhs.ps1 search --cursor "CURSOR" --max-chars 6000
```

回读续页保存为新的 UTF-8 JSON，再用 `read --input` 执行：

```json
{"cursor":"CURSOR","max_chars":12000}
```

续页只传 `cursor` 和可选的 `max_chars`，不能混入新关键词、ID 或上下文范围。`continued` 表示该篇承接上一页，`has_more` 表示该篇的本次读取范围仍有内容在下一页；它们不代表整篇或整夹已经完整归档。

| 返回状态 | 应如何处理 |
| --- | --- |
| `partial=true` / 退出码 `3` | 检查 `warnings`、`data.errors` 和各篇 `gaps`，保留成功项 |
| `CURSOR_STALE` | 资料库或策略已变化，重新发起原始请求 |
| `OUTPUT_BUDGET_TOO_SMALL` | 增大字符预算或减少单次请求的笔记数 |
| `CANDIDATES_LIMITED` | 候选通道触及上限，不能声称已经穷尽搜索 |
| 空 `items` | 本轮没有返回候选；不证明尚未归档的收藏没有相关内容 |

stdout 输出单个 JSON；stderr 用于诊断。退出码 `0` 为成功、`2` 为输入错误、`3` 为部分成功、`4` 为状态或对象问题、`5` 为运行失败。

`coverage` 描述指定范围内的归档状态；`body_available` 是保存状态计数，包含纯标签正文，不等于有效文章数或搜索召回率。`unavailable_body_matches` 与 `noncontent_body_matches` 统计本轮已枚举候选的两种正文缺口，也不是整个资料库的待处理数量。

## 图片：先检查，再按需获取

只读检查已有图片、正文中的图片提示和缺口：

```powershell
.\scripts\xhs.ps1 images plan --note NOTE_ID
.\scripts\xhs.ps1 images plan --collection COLLECTION_ID --limit 8
.\scripts\xhs.ps1 images plan --cursor "CURSOR" --max-chars 6000
```

`plan` 只读检查本机资料。`body_image_hints` 给出正文提到图片的位置。项目在规划、回读和补图进度中都检查文件可读且非空，并核对归档中已有的字节数和 SHA-256。`media.corrupt_local_files` 是校验失败的文件数。

阅读已保存的图片，使用固定版本的 asset 请求：

```json
{
  "items": [{"note_id":"NOTE_ID","revision_id":"REVISION_ID"}],
  "mode":"asset",
  "max_chars":12000
}
```

Agent 需实际打开 `assets[].path` 才能判断图中内容。没有图片时仍会返回该笔记及原因，不能把空数组当作“图片已读完”：

| 缺口 | 含义 |
| --- | --- |
| `MEDIA_DEFERRED` | 尚未获取图片 |
| `IMAGE_COUNT_UNKNOWN` | 未确认总数，不能声称所有图片齐全 |
| `IMAGE_LIST_INCOMPLETE` / `IMAGE_LIST_UNVERIFIED` | 已知清单不全或未经确认 |
| `ASSET_MISSING` | 图片没有可读取的本机文件 |
| `ASSET_CORRUPT` / `ASSET_UNREADABLE` | 图片文件损坏或当前无法打开。该项带图片 ID，同批通过校验的图片仍返回路径 |
| `IMAGES_CAPTURED_SEPARATELY` | 正文与保留的图片分次采集，不是同一次网页快照 |

只有用户要求补图时，才执行获取命令：

```powershell
.\scripts\xhs.ps1 images fetch --note NOTE_ID
.\scripts\xhs.ps1 images fetch --collection COLLECTION_ID --background
.\scripts\xhs.ps1 images status --job JOB_ID
.\scripts\xhs.ps1 images status --job JOB_ID --limit 50 --cursor CURSOR
.\scripts\xhs.ps1 images pause --job JOB_ID
.\scripts\xhs.ps1 images resume --job JOB_ID
.\scripts\xhs.ps1 images process --job JOB_ID
```

`fetch` 和扩展使用同一补图入口。已有完整图片清单时直接补下载，需要清单时由已连接扩展的浏览器读取。命令优先交给运行中的本机组件，资料库空闲时可先处理已有清单的下载。`--background` 只排队。等待浏览器时保持浏览器和扩展连接，`images process` 可处理已取得清单的下载。

`resume` 沿用原任务并恢复浏览器读取，已下载文件保留。扩展中已确认的暂停也可通过 CLI 显式继续，尚未送达本机的暂停操作会优先生效。同收藏夹已有暂停或旧匿名任务时，再次 `fetch` 会复用并继续该任务。按收藏夹补图处理本地已归档成员。

`images status --job JOB_ID` 默认返回前 50 篇结果，可用 `--limit` 调整为 1–100 篇。`data.next_cursor` 有值时，带同一任务 ID 和 `--cursor` 继续，直到游标为空。每页都返回整个任务的实时汇总，每项保留 `note_id` 和成功保存后的 `revision_id`，可直接用于固定版本回读。任务进行中可以翻页，任务完成后可从首页核对完整结果。

成功补图产生新 `revision_id`，之后按新版本读图片。图片任务不改写正文、不自动运行 OCR；新下载图片不会自动进入文本检索。懒加载可能导致图片不全，页面提供的图片也不保证是最高分辨率。真实站点补图尚未验收，视频处理尚未实现。

## 用扩展保存正文和图片

安装本机组件后，在 Chrome / Edge 的扩展管理页加载项目的 `browser-extension` 文件夹。首次加载或升级后确认扩展连接正常；已有扩展需点击“重新加载”。

1. 打开小红书收藏页面，例如个人主页的“收藏 → 笔记”。
2. 打开扩展，点击“读取当前收藏夹”，核对列表范围、名称与总数。
3. 列表扫描期间保留原收藏标签页。正文保存数、已发现数和图片进度分别查看；列表停止增长不等于列表完整。
4. 如需图片，打开已进入资料库的笔记详情，点击“当前笔记图片”，或使用“收藏夹图片”。后者先核对本地图片，已齐全的笔记直接略过；已有完整列表但缺少文件时直接补下载。需要列表的笔记才打开后台页，读取图片列表后关闭，再由本机下载。请保持浏览器打开。

图片任务暂停后，“继续补图”沿用原任务，通过浏览器补全需要更新的图片列表，并保留已下载的图片。旧匿名任务遇到登录墙时，继续也会切换到浏览器读取；普通失败用尽尝试次数后，手动继续可以重新尝试。若页面仍要求登录或验证，会再次暂停并显示原因。未知总数或未加载齐的图库仍标为待补全，不会反复自动打开同一篇。

补图分为“读取图片列表”和“保存图片”两步。后台页会识别 `1/17` 等分页总数，并在有限时间内切换图库以加载缺失图片；你切到前台的页面不会自动翻图。每保存一张图片就更新进度，不必等整篇结束。只读到一部分时保留已存文件；后续取得的图片只有序号、总数及版本证据一致时才合并判定齐全。图片列表最多提前读取 2 篇，避免下载尚未推进就大量打开页面。正文和图片页交替读取。

扩展可以在其他网页显示已有任务进度。浏览器正文补全遇到标签页忙碌、意外关闭或加载超时，会等待约 5 秒、15 秒重试同篇，总计最多尝试 3 次。本机断线后会重连，并优先重交尚未确认保存的结果。手动暂停、登录验证和平台限制不会被自动恢复覆盖；升级前已暂停的正文任务需要显式继续。

识别失效或完成读取后，扩展会立即清理自己打开的后台页，不等待本机确认结果；断线、重启恢复、关闭失败也会继续清理。你主动激活或导航到其他内容的页面会保留。确认失效的笔记直接跳过，不消耗普通故障的重试次数。

页面明确显示“找不到对应的笔记”、笔记已删除、笔记不存在，或确认访问链接失效时，该笔记按 ID 加入资料库的持久失效列表，自动继续下一篇。刷新、更新收藏夹、跨收藏夹扫描、重启、ID 大小写或链接参数变化都不会让它再次进入正文或补图读取队列。旧正文和图片任务里已确认的失效项也会迁入此列表。面板分别显示正文保存数和失效跳过数，可展开“失效列表”查看笔记和原因；跳过不算正文保存成功，之前保存的正文仍保留。仅仅解析不到正文、“暂时无法浏览”、网络故障、登录验证和安全限制不会被当成笔记已删除。安全限制和登录要求优先于失效判断。

可用 `xhs-context invalid list --limit 50 --offset 0` 只读查看失效列表，`has_more=true` 时增加 offset 续页；若读取期间 total 改变，应从 offset 0 重新读取，面板会自动处理这种变化。列表读取不联网，不清除失效记录，也不恢复采集。

真实批量读取曾遇到平台安全限制 `300013`。`300013` / HTTP `429` 会暂停同一资料库的正文和图片获取，并显示原因。处理平台提示后，点击扩展面板的“刷新”可清除本机保存的旧限制状态，再手动点击继续读取；刷新本身不读取收藏，也不代表平台已解除限制。如果再次读取仍遇到限制，任务会再次暂停。定时更新状态、重新打开面板和重启不会自动清除限制。

本地已保存正文、图片仍可离线查询。查询任务不自行解锁、探测平台是否恢复或启动同步。

## 数据位置与验证边界

安装配置通常位于 `%LOCALAPPDATA%\xhs-context\config.json`，CLI 与浏览器本机组件共用资料库。正文、图片对象和历史版本保存在本机；Agent 读取后的数据流向取决于所用 Agent 与模型服务，不能把本地保存等同于推理也在本地完成。

图片按内容哈希复用；补图和更新正文产生固定版本。源码与发布包不应包含真实收藏、本机配置、凭据或个人评测结果。备份、恢复、软删除和开发接口见 [协议说明](protocol.md)。

本项目当前面向 Windows / Chrome / Edge。自动化验证使用合成资料和隔离浏览器，不等同于真实收藏全量覆盖、长期采集可靠性或真实补图已通过验收。最新验证范围见 [验证记录](validation.md)；新聊天自动选用 Skill 和 DeepSeek Harness 接入仍未验收。
