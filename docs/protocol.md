# 原型协议与边界

当前同步范围为正文（content_scope=body）：保存标题、正文、来源和版本，不下载图片、不运行 OCR。媒体仍是后续目标；历史版本与显式导入的既有媒体保持可读。

## 本机同步接口

个人主页的 `?tab=fav&subTab=note` 直接作为收藏笔记入口。扩展从选中的“笔记”标签读取总数，只枚举当前可见收藏内容区域，排除仍留在 DOM 中但被隐藏的已发布、点赞列表。收藏总览使用本地名称“收藏 · 笔记”，不冒充平台上的命名收藏夹。

页面身份保留 `tab`、`subTab` 和文件夹参数，以免把“笔记 / 专辑 / 文件”混为一个列表；读取中切换范围会停止本次扫描。卡片可使用 `/explore/笔记ID`、`/discovery/item/笔记ID` 或 `/user/profile/用户ID/笔记ID`，后者以最后一段识别笔记。保存页面实际可点击的链接及其访问参数，不使用隐藏的无参数链接替代。

扩展默认使用 Native Messaging：浏览器自动启动 `xhs-context-native.exe`，通过 stdin/stdout 传输长度前缀为 4 字节小端整数的 UTF-8 JSON。请求格式为 `{id, path, body?}`，响应为 `{id, ...envelope}`；请求最多 2 MiB，响应小于 1 MiB。下表中的 GET 表示省略 body，POST 表示提供 body，不要求运行 HTTP 服务。

一次性安装在 HKCU 注册 Chrome / Edge 宿主 `org.xhs_context.host`，仅允许固定扩展 ID `mehmgdlepmchnakhfmcbhdcgciphljpb`。扩展清单的公钥仅用于稳定 ID，不是秘密。网页内容脚本无法调用任意本机方法；不申请 Cookie、任意站点访问或 HTTP 本机访问权限。

本机组件随原生端口存活；关闭弹窗不影响它，浏览器关闭后进程退出。仅运行一个资料库 worker；同时开启旧 HTTP 同步服务或另一个浏览器会报告占用，不能并发处理同一资料库。

安装配置位于 `%LOCALAPPDATA%/xhs-context/config.json`；CLI 与本机组件共用默认库。`--library`、`XHS_CONTEXT_HOME` 可以显式覆盖。`sync serve` 仅供开发调试，仍绑定 127.0.0.1 并校验 Bearer token、Origin 和 Host。

| 方法与路径 | 负载或结果 |
| --- | --- |
| POST /v1/jobs | job_id、collection（id/name/source_url/identity_kind）、expected_count |
| POST /v1/jobs/ID/batches | seq（从 0 连续编号）、items（note_id/source_url/title，最多 50 条） |
| POST /v1/jobs/ID/finish | batch_count、end_evidence |
| POST /v1/jobs/ID/pause | 暂停处理 |
| POST /v1/jobs/ID/resume | 恢复处理，重试次数未满的失败项重新排队 |
| POST /v1/jobs/ID/browser | 显式启用普通浏览器笔记页补全；首次切换为未完成项分配新的尝试次数 |
| POST /v1/browser/next | 返回一个带 lease_id 的待读取笔记，或 task=null |
| POST /v1/jobs/ID/browser-result | note_id、lease_id，以及 snapshot 或 error |
| POST /v1/access/restrict | error=ACCESS_RESTRICTED；记录安全限制并停止本库所有任务；无解除接口 |
| GET /v1/status?job_id=ID | 范围、批次、内容处理与失败摘要 |

扩展每批最多 25 条。批次内容哈希用于幂等校验：相同 job_id + seq 的相同内容可重发，不同内容返回 CONFLICT。ACK 只在数据库事务提交后返回。

同一收藏视图同时只允许一个未结束扫描，避免交错扫描错误更新成员关系。结束列表扫描后仍可暂停或恢复内容处理；此时恢复只重试未完成内容，不重新提交列表批次。

扫描终点依据是 page_end、user_verified、unknown 或 interrupted。只有前两种、批次连续且预期数量匹配时列表才完整。unknown 和 interrupted 永远不触发成员移除。两个独立完整扫描均未见到旧成员后才标记 removed；重复的 finish 请求不重复累积缺席。

一个库只运行一个处理 worker，使用操作系统文件锁；短写事务由 SQLite 串行化。重启会恢复中断的 running 项。单项最多尝试 3 次；普通登录墙、验证提示或加载超时暂停所在任务。安全限制 300013 与 HTTP 429 触发库级停止保护，不能用普通恢复操作清除。

0.3.2 将 `access_restriction` 写入 SQLite meta；全局 status 和 job 报告都返回该字段。保护存在时新建任务、resume、browser 返回 ACCESS_RESTRICTED，browser/next 返回 task=null 与限制信息，匿名 worker 不发起请求。所有现有任务均为 paused；普通 pause 保留停止原因。扩展停止列表滚动，禁用启动、继续和浏览器补全；新建/恢复须经本机确认后才开始滚动。浏览器识别 300013 后先持久记录未提交的停止信号，若本机断线，下次先补交停止信号再考虑领取任务。网站错误 300017 单独标记 SOURCE_LINK_EXPIRED，不冒充登录或安全限制。用户已能正常阅读的笔记内提及 300013 不触发保护。

该保护不自动解锁、不自动探测小红书恢复情况、不声称限制只与登录或访问频率有关。当前请求可能已经发出，保护阻止的是后续自动调度；本地查询、导出、备份和固定版本回读仍可使用。新检测到的限制会停止同一资料库的任务，不控制其他工具或其他资料库。

只有在人工确认正常访问已恢复、并重新评估读取方式后，才可执行 `sync unlock --confirm-access-restored` 清除本地标记。该命令不访问小红书，也不恢复任何任务；仍需另行显式 resume。它不代表平台解除限制的证明，当前实测任务不得为了验证此命令而继续访问。

任务在扩展本地日志中持久化，当前网页只决定能否新建扫描，不决定能否查看已有任务。进度、暂停和内容重试在任何网页可用；未完成的列表扫描仍使用原标签页。面板分别显示已发现数、正文保存数和队列状态，failure_summary 汇总正文失败。body_complete 只有在列表完整且本轮每项正文均成功时才为 true；content_complete 仍代表包含媒体的完整归档，不能因为正文成功就置为 true。

浏览器补全由面板按钮显式启用，不在匿名失败后自动切换。扩展每次只打开一个非活动笔记标签页，从标题和正文 DOM 读取快照；不读取图片轮播、Cookie、私有接口、网页内部状态或评论区。单页最多等待 45 秒；页面租约 90 秒，同一库最多一个 browser_running 项。暂停后不再发布在途网页结果，重复提交同一租约幂等，过期租约不可覆盖新结果。只关闭仍在该笔记地址、且未被用户激活的自有标签页。

快照验证笔记 ID 和实际页面地址，记录 content_source=browser_dom。正文在同一事务中保存、索引，并将任务项标记 ready；没有后续图片任务。结果记录 content_scope=body、content_state=body_only、media_state=deferred 和 MEDIA_DEFERRED；这是本版未处理的范围，不是正文失败。正文为空、缺失或仅有摘要时分别记录 BODY_EMPTY、BODY_MISSING、BODY_UNVERIFIED，不计为正文成功。匿名请求沿用用户配置的 HTTP/HTTPS 系统代理，并在每个跳转上校验来源及 TLS；代理不可用和本机 DNS 异常会暂停并报告。

更新缺少已有正文时保留原版本及其索引；正常取得新正文则发布新的固定版本，不把旧图片拼入新正文。升级时将“正文已取得、仅媒体失败”的旧任务项转为正文完成，保留旧修订、旧对象和引用。媒体失败项不再循环重试。

## 检索接口

0.4.0 的响应信封使用 `schema_version=2`。本轮调整了 search 默认筛选和 read 返回结构；SQLite 存储版本仍为 1，已有原文、修订、证据 ID、队列和停止保护无需迁移，索引无需重建。工作区的正文内容过滤将搜索策略升级为 `lexical-body-v3`；先前搜索游标返回 `CURSOR_STALE`，客户端应重新发起原请求。固定版本 read 的格式与游标策略不变。

search JSON 字段：

| 字段 | 约定 |
| --- | --- |
| queries | 1–4 个关键词查询，合并候选并按笔记去重；不是自然语言问答接口 |
| collection_ids | 可空；指定时限定收藏范围 |
| content | `body`（默认）仅返回已有正文且去除标签后仍含文字或数字的笔记；`all` 同时保留缺失正文、纯标签正文的标题或既有 OCR 线索 |
| required_terms | 最多 4 个必要条件；每个条件都须在标题、正文或既有 OCR 中命中，支持等价名称，仅标签命中不满足条件 |
| limit | 1–50，每页条数，不是全量结果上限 |
| max_chars | 512–200000，完整 JSON 的字符预算 |

候选由 FTS 和字面通道取得，每个展开查询的候选通道最多取 1000 条；触及上限返回 `CANDIDATES_LIMITED`，不能声称穷尽。排序区分标题、正文、OCR 与标签，实质词命中优先于靠标签凑齐条件；等价查询只取组内最强分数，避免重复写 Madrid/马德里抬高排名。CLI 仅处理等价名称，不把 infra 自动映射为容器、网络等关联主题。进一步查哪些主题由 Agent 根据任务和已读原文决定。

`data.items` 返回固定版本、`matched_queries`、`matched_fields` 与最多两段原文摘录；长段围绕命中截取，不以开头固定截断替代。`snippets[].location` 的正文 start/end 对应原文字符位置，`excerpt_only` 表示不是整个证据块，`matched` 区分命中片段与无直接段落命中时的介绍。原始正文不因标签降权或摘录而改写。

`unavailable_body_matches` 是本次已枚举候选中正文未取得、未经验证或为空白的匹配数；`noncontent_body_matches` 是另一个互斥计数，表示正文已保存且非空，但去掉标签后没有文字或数字（包括纯标签、不可见字符及纯装饰符号）。默认模式在排序分页前将两类候选从 items 和 matched_count 排除，避免它们占用结果页。`content=all` 保留两类线索，并分别附上 `body_filter_reason=unavailable` 或 `no_text`；正常正文不附该字段。

过滤只判断是否有标签之外的文本，不设最短字数，也不因“见图”“视频”等词排除整篇；它不声称判断教程完整性或语义质量。原文、修订、固定版本读取和索引不变；搜索摘录也跳过纯标签或装饰段落。两个匹配计数都不是整库待处理数；`coverage` 描述范围内的保存状态，`body_available` 仍包含纯标签正文，不是有效文章数或召回率。必要条件约束整篇笔记，同篇出现多个城市时仍需 Agent 逐段归属。

read JSON：items（1–10 篇，每项指定 note_id/revision_id，可指定 block_ids 或 asset_ids）、mode（evidence/full/asset）、max_chars，或者 cursor 与 max_chars。同一请求不重复提交相同的 note_id/revision_id。

read 的 `data.items` **按笔记聚合**。每篇每页共享 note_id、revision_id、title、source_url、来源类型、内容范围和 gaps；evidence/full 模式返回 `blocks[]`（block_id、kind、原文 text、location），asset 模式返回 `assets[]`。不再为每段重复笔记元数据，不再返回单个 `block` / `asset` 或冗余 ordinal。数组保持原文顺序，证据 ID、内容和原始位置保留。

`continued` 表示本篇承接上一页，`has_more` 表示本篇的本次请求内容仍在下一页；它们不代表整篇或整夹已完整归档。`data.next_cursor` 指向本次请求的剩余结果，`data.errors` 保留逐项错误。`mode=evidence` 未指定块时从开头取少量上下文，指定块时包含每个锚点的相邻块；全文核对使用 `full`。

续页只发送 cursor 和可选 max_chars，不能混入新查询条件。搜索游标绑定请求、检索策略版本和 index generation。游标校验与所有检索读取位于同一读事务；读取游标绑定固定笔记版本和返回格式版本。输出预算包含整个 JSON，不会输出被切断的 JSON；如果单个证据单元不能放入预算，返回 OUTPUT_BUDGET_TOO_SMALL。

空结果、缺少版本、缺少媒体、OCR 未完成分别表达。逐项失败保留成功项，并返回 partial=true、退出码 3。

## 可选 CLI 操作

日常读取收藏夹使用扩展。Agent 查询或资料管理可在项目目录执行：

```powershell
.\scripts\xhs.ps1 status
.\scripts\xhs.ps1 search --query "AI Infra" --query "Agent 权限"
.\scripts\xhs.ps1 search --query "Madrid tapas" --query "马德里 可颂" --require "马德里"
.\scripts\xhs.ps1 search --query "infra" --content all
.\scripts\xhs.ps1 search --input examples\search.json
.\scripts\xhs.ps1 read --input read.json
.\scripts\xhs.ps1 export
.\scripts\xhs.ps1 backup --destination .\var\backup-001
.\scripts\xhs.ps1 --library .\var\restored restore --source .\var\backup-001
```

read.json 示例，ID 取自搜索结果：

```json
{"items":[{"note_id":"NOTE_ID","revision_id":"REVISION_ID","block_ids":["BLOCK_ID"]}],"mode":"evidence","max_chars":12000}
```

`evidence` 返回指定块和邻近上下文，`full` 按顺序读取，`asset` 用 `asset_ids` 返回本机原图路径。复杂请求使用 UTF-8 JSON 文件或 `--input -`。stdout 为单个 JSON，stderr 为诊断；退出码 0 成功、2 输入错误、3 部分成功、4 状态或对象问题、5 运行失败。`max_chars` 是完整 JSON 的 Unicode 字符预算，不是精确 token 数。

备份及恢复目标必须为新目录。`exports/current.json` 指向完整阅读快照；备份包含数据库及引用对象，不含配对信息。其他管理命令有 `sync pause --job ID`、`sync resume --job ID`、`reindex`、`note delete ID` 和 `note restore ID`；删除是软删除。

## 已知实现限制

- 页面适配器使用收藏卡片和笔记正文 DOM，以及常见列表终点元素；它不监听私有接口、不读取 Cookie。网站改版、缺失或过期链接仍可能造成正文缺口。大收藏列表的全量覆盖仍需单独核对。
- 平台文件夹 ID 未作普遍验证，扩展按页面地址和可见视图标记保存 local_view 身份。自动识别名称不等于确认原平台文件夹 ID；名称与总数可手动修正。
- 匿名页面解析只接受可解析的序列化初始状态，不执行网页 JavaScript。只有识别到笔记详情结构才将正文标记为已取得；仅命中信息流预览时记录 BODY_UNVERIFIED，不计为正文完成。没有支持的状态时失败，保留上一个可用版本。这种结构判断仍需真实页面人工核对。
- 本版不获取图片或视频。图片笔记中的信息没有进入正文检索；asset 请求遇到媒体未处理时明确返回 MEDIA_DEFERRED。已有本地媒体及旧版本证据保留。
- 确认停止增长不代表完整；扩展等待用户核对。暂停时允许当前网络请求结束，但不再发布尚未完成的处理结果。
- 每次新扫描都会重新尝试已枚举笔记的正文读取。版本和对象按哈希去重，刷新频率策略尚未优化。
- 搜索是中文分词、字面匹配、少量别名与相关性融合；没有 embedding 或重排模型。
- 本地删除是软删除；尚未提供历史版本和对象的物理清理功能。
- 安装脚本、锁文件和源码针对 Windows 原型交付；没有声称其他操作系统已经验收。
- Google Maps 保存、真实收藏效果评测、Harness 接入不包含在已完成的本地自动化测试中。

## 代码来源

本原型参考计划书中的公开资料与页面数据格式独立实现，没有复制“看看收藏”的实现文件。上游项目的许可与固定提交见研究记录；未来若直接移植代码，应另行记录具体来源和许可。

