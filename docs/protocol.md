# 原型协议与边界

默认收藏同步保存正文（content_scope=body）。0.5.0 新增独立图片任务：正文和图片可分次取得，图片下载不启动 OCR、不重新导入正文。历史版本与既有媒体保持可读。

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
| POST /v1/browser/next | 正文与图片列表交替领取，共享一个浏览器读取租约；图片 task 带 kind=images，均带 lease_id，或 task=null |
| POST /v1/browser/check | job_id、note_id、lease_id，图片另传 kind=images；只读返回 allowed；核对租约、暂停、失效记录及全局限制 |
| POST /v1/jobs/ID/browser-result | note_id、lease_id，以及 snapshot 或 error |
| POST /v1/access/restrict | error=ACCESS_RESTRICTED；记录安全限制并停止本库所有任务 |
| POST /v1/access/refresh | 空 JSON 对象；用户手动刷新时清除本地停止标记，返回 unlocked 与 jobs_resumed=false；不访问平台、不恢复任务 |
| GET /v1/status?job_id=ID | 范围、批次、内容处理与失败摘要 |
| GET /v1/invalid-notes?limit=50&offset=0 | 只读持久失效列表；total、items、has_more；limit 1–100，offset 0–100000 |
| POST /v1/image-jobs | note_ids、collection_id、snapshot 三选一，可传 job_id 实现请求幂等。按笔记或收藏夹补图默认由浏览器取得需要的图片清单 |
| GET /v1/image-jobs/ID?limit=50&cursor=CURSOR | 整个任务的实时进度与一页结果，limit 为 1–100，next_cursor 用于续页 |
| POST /v1/image-jobs/ID/pause | 暂停图片队列，不影响正文任务 |
| POST /v1/image-jobs/ID/resume | 显式恢复原任务并启用浏览器读取，为失败／不完整项重置尝试预算，保留下载检查点，遵守停止保护 |
| POST /v1/image-jobs/ID/browser | 显式切换为浏览器图片列表并恢复原图片任务；用于扩展“继续补图” |
| POST /v1/image-jobs/ID/browser-result | note_id、lease_id 及 snapshot/error；图片列表交给本机下载，迟到或重复结果不改变新租约 |

扩展每批最多 25 条。批次内容哈希用于幂等校验：相同 job_id + seq 的相同内容可重发，不同内容返回 CONFLICT。ACK 只在数据库事务提交后返回。

同一收藏视图同时只允许一个未结束扫描，避免交错扫描错误更新成员关系。新建时若已有未结束扫描，返回 `SCAN_ALREADY_RUNNING`（details 含 job_id、updated_at）；若该扫描 30 分钟没有新批次或状态更新（通常是另一浏览器或扩展本地记录丢失），新任务会接管，旧扫描按 interrupted 结束并在结果中以 `superseded_job` 返回，不移除任何成员。也可用 `sync abandon --job ID` 立即结束。已结束扫描再收到批次返回 `SCAN_FINALIZED`，扩展据此丢弃该扫描的待发批次并停止滚动。结束列表扫描后仍可暂停或恢复内容处理；此时恢复只重试未完成内容，不重新提交列表批次。

扫描终点依据是 page_end、user_verified、unknown 或 interrupted。只有前两种、批次连续且预期数量匹配时列表才完整。unknown 和 interrupted 永远不触发成员移除。两个独立完整扫描均未见到旧成员后才标记 removed；重复的 finish 请求不重复累积缺席。

一个库只运行一个处理 worker，使用操作系统文件锁；短写事务由 SQLite 串行化。重启会恢复中断的 running 项；浏览器仍可能持有页面或待提交结果，因此保留其未到期租约，遗失租约由 next_page 在 90 秒后回收。

浏览器临时错误 BROWSER_BUSY、BROWSER_TIMEOUT、BROWSER_CLOSED、BROWSER_INTERRUPTED 在第 1、2 次失败后分别等待 5 秒、15 秒，总计最多尝试 3 次。重试时间保存在 job_items.payload 的 browser_retry_at 中，status 的 job.browser_retry 返回错误、尝试次数及毫秒时间戳；冷却期间不打开后续笔记。达到上限才暂停，用户显式继续可重新分配临时错误预算。普通登录墙或验证提示仍直接暂停；安全限制 300013 与 HTTP 429 触发库级停止保护，不能用普通恢复清除。用户手动刷新清除本地标记后，显式继续会为限制失败项重新分配尝试预算，避免过去的限制耗尽次数后永久卡住；新的真实限制仍立即暂停所有任务。

扩展用单条 browser_outbox 持久保存待确认的正文或错误结果，断线后先重交再领下一篇。本机断开按 5、10、20、30 秒上限延迟重连；这是本机通信，不会在断线期间访问新笔记。后台闹钟可能使实际等待更长。遗失页面的 90 秒租约过期后优先重试同篇，连续 3 次过期也暂停；手动暂停后的过期记录归还尝试次数。手动暂停信号在本机断线时也保留，重交结果不能据此自动恢复任务。

0.3.2 将 `access_restriction` 写入 SQLite meta；全局 status 和 job 报告都返回该字段。保护存在时新建任务、resume、browser 返回 ACCESS_RESTRICTED，browser/next 返回 task=null 与限制信息，匿名 worker 不发起请求。所有现有任务均为 paused；普通 pause 保留停止原因。扩展停止列表滚动，禁用启动、继续和浏览器补全；新建/恢复须经本机确认后才开始滚动。浏览器识别 300013 后先持久记录未提交的停止信号，若本机断线，下次先补交停止信号再考虑领取任务。网站错误 300017 单独标记 SOURCE_LINK_EXPIRED，不冒充登录或安全限制。用户已能正常阅读的笔记内提及 300013 不触发保护。

该保护不自动解锁、不自动探测小红书恢复情况、不声称限制只与登录或访问频率有关。当前请求可能已经发出，保护阻止的是后续自动调度；本地查询、导出、备份和固定版本回读仍可使用。新检测到的限制会停止同一资料库的任务，不控制其他工具或其他资料库。

用户手动点击扩展的“刷新”调用 `/v1/access/refresh`，清除本地停止标记及任务级限制错误，正文和图片任务仍为 paused；用户再次点击读取才允许新的尝试。自动状态轮询、重新打开面板和重启不会调用该接口，也不会解锁。扩展在清除前先停止扫描并交付待确认的限制报告和读取结果，避免旧报告在刷新后重新锁定。刷新不探测平台、不证明平台已经恢复；新一次读取若仍出现真实限制，重新持久记录并暂停。无停止标记时刷新不改变任务状态。

扩展日志 `acquisition_epoch` 记录取消代次；安全停止及显式暂停使旧的开始、继续、浏览器补全和补图操作失效。异步准备完成后、发送激活请求前，以及返回后更新会话／开始扫描时均核对代次。积压批次、迟到响应不能在刷新后自动恢复；用户新点击的操作使用新代次。无须等待网络结果才能取消旧操作。

CLI 保留 `sync unlock --confirm-access-restored`，供人工确认访问后清除本地标记；同样不访问小红书、不恢复任务，仍需另行显式 resume。查询任务不会自行调用刷新或解锁接口。

任务在扩展本地日志中持久化，当前网页只决定能否新建扫描，不决定能否查看已有任务。进度、暂停和内容重试在任何网页可用；未完成的列表扫描仍使用原标签页。面板分别显示已发现数、正文保存数和队列状态，failure_summary 汇总正文失败。body_complete 只有在列表完整且本轮每项正文均成功时才为 true；content_complete 仍代表包含媒体的完整归档，不能因为正文成功就置为 true。

明确的单篇失效 `SOURCE_UNAVAILABLE` 与链接失效 `SOURCE_LINK_EXPIRED` 记入 `job_items.state=skipped`，保留错误原因并继续下一篇；不会暂停库或任务，不覆盖已有正文和版本。普通 resume / browser 不重新排队 skipped；旧版 failed 的这两类错误在显式继续时转换为 skipped。解析未找到目标的 `NOTE_NOT_FOUND` 仍属未确认失败，不等同于笔记删除。

job 报告的 `skipped_count`、`skipped_summary` 单独汇总跳过项；`read_complete` 表示列表已收齐且所有项均为 ready 或 skipped。`body_complete` 仍要求所有项为 ready，不能用跳过填补正文覆盖。面板在有跳过项时显示“读取结束，已跳过 N 篇失效笔记”。

`invalid_notes` 按 note_id 保存库级失效记录（原因、首次确认的来源 URL、时间和任务 ID），相同十六进制 ID 的大小写等价，历史主键与版本保持原样。检测到明确笔记失效时同步停止同一 ID 的待处理正文与图片项；后续新任务即使跨收藏夹或链接参数变化，也直接记 skipped，不产生第二次请求。旧 job_items 和 image_items 中纯 SOURCE_UNAVAILABLE / SOURCE_LINK_EXPIRED 的 failed/skipped 在升级时幂等回填。此前已保存的正文、图片和版本不删除；图片文件本身的 404 不推断为整篇笔记失效。

永久失效的页面文字必须是独立肯定的删除或不存在提示；引用、否定、临时故障、重试和登录提示不能据此建立永久记录。匿名 HTML 无法验证样式表控制的可见性时保留未确认状态；浏览器按可见 DOM 判断。笔记请求重定向到其他笔记或无关页面时，不把其他页面的错误记到原笔记。浏览器错误页先等待加载并检查安全／登录提示，再确认链接失效。

全局 status 的 `invalid_notes.total` 提供总数，面板“失效列表”及 CLI `invalid list --limit 50 --offset 0` 分页显示条目。分页为实时列表，total 改变时从 offset 0 重读，面板自动重读以避免条目移位造成重复或遗漏。清除安全限制、重新安装或普通继续均不清除失效记录；当前没有自动或隐式复活入口。

浏览器正文补全由面板按钮显式启用，不在匿名失败后自动切换。扩展每次只打开一个非活动笔记标签页，从标题和正文 DOM 读取快照；正文任务不读取图片轮播、Cookie、私有接口、网页内部状态或评论区。单页最多等待 45 秒；页面租约 90 秒，同一库最多一个 browser_running 项。暂停后不再发布在途网页结果，重复提交同一租约幂等，过期租约不可覆盖新结果。读完先保存结果并清理自有后台页，再等待本机确认；关闭失败保留归属重试。清理限原笔记或已确认站内错误页，用户激活或导航到其他内容的页面保留。

快照验证笔记 ID 和实际页面地址，记录 content_source=browser_dom。正文在同一事务中保存、索引，并将任务项标记 ready；没有后续图片任务。结果记录 content_scope=body、content_state=body_only、media_state=deferred 和 MEDIA_DEFERRED；这是本版未处理的范围，不是正文失败。正文为空、缺失或仅有摘要时分别记录 BODY_EMPTY、BODY_MISSING、BODY_UNVERIFIED，不计为正文成功。匿名请求沿用用户配置的 HTTP/HTTPS 系统代理，并在每个跳转上校验来源及 TLS；代理不可用和本机 DNS 异常会暂停并报告。

更新缺少已有正文时保留原版本及其索引；正常取得新正文则发布新的固定版本，保留先前单独获取的图片并标明分次采集。升级时将“正文已取得、仅媒体失败”的旧任务项转为正文完成，保留旧修订、旧对象和引用。正文任务不循环重试媒体失败项。

## 检索接口

搜索和所有回读模式按笔记返回可选的 `invalid_source`，其中 `error` 为 SOURCE_LINK_EXPIRED 或 SOURCE_UNAVAILABLE，`observed_at` 是首次确认时间。这是本次查询时已知的来源状态，可随旧版本正文一起返回。字段缺省表示没有确认记录。该信息不参与排序，也不改变 revision_id。

工作区的响应信封仍为 `schema_version=2`。SQLite 存储版本仍为 1，初始化时幂等补建 image_jobs / image_items 两张表，已有原文、修订、证据 ID、正文队列和停止保护保留，索引无需重建。尚未发布的搜索策略为 `lexical-body-v6`，读取策略为 `grouped-evidence-v4`；旧游标返回 `CURSOR_STALE`，客户端应重新发起原请求。

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

**等价名称。** 内置组只有 Madrid/马德里、Kubernetes/k8s、infra/基础设施、AI Infra/AI 基础设施。资料库可用 `aliases add 名称 名称…` 新建或扩展组、`aliases remove 名称` 删除自定义名称、`aliases list` 查看全部组及来源（builtin / extended / custom）；内置名称不可删除，一组最多 12 个名称，不会合并两个内置组。自定义组保存在数据库 meta 中，随备份恢复；修改会递增 generation，使原搜索游标失效。`effective_queries` 列出本次实际展开的写法，用户自己的写法排在组内第一位。这是资料库管理操作，查询任务不应自行增删。

**v6 部分命中层（仅 `match=terms`）。** 每个查询仍先要求全部关键词出现在同一篇笔记（完整命中），其排序与 v5 相同。若某篇笔记对至少一个多词查询只在标题、正文或 OCR 中命中了不少于一半的词（不能只靠单个汉字或标签），它进入第二层，排在所有完整命中之后，并附 `partial_match: [{query, missing_terms}]` 说明缺哪些词。`matched_count` 仍只计完整命中，`partial_term_matches` 计第二层数量；分页游标按“完整命中 → 部分命中”的顺序连续翻页。正文过滤计数 `unavailable_body_matches` / `noncontent_body_matches` 只统计完整命中。部分命中的候选通道另有 1000 条上限，触及时返回警告 `PARTIAL_CANDIDATES_LIMITED`，但不置 `partial=true`，因为完整命中不受影响。部分命中只是线索：缺少的词可能正是问题的必要条件，需要回读确认或改用 `required_terms`。`match=phrase` 不产生部分命中。

查询词两端的标点不再参与匹配：`learn k8s.` 中的 `k8s.` 视为 `k8s`，`“马德里”`、`（C#）` 的字面通道会去掉引号和括号；`C++`、`C#`、`node.js` 等标识中的符号保留。

`data.items` 返回固定版本、`matched_queries`、`matched_fields` 与最多两段原文摘录；长段围绕命中截取，不以开头固定截断替代。`snippets[].location` 的正文 start/end 对应原文字符位置，`excerpt_only` 表示不是整个证据块，`matched` 区分命中片段与无直接段落命中时的介绍。原始正文不因标签降权或摘录而改写。

v5 保持笔记候选与排序规则；摘录在词匹配分数相同时，以完整短语命中、重复标题与句末标点作弱排序信号。编号后的句点不算句末。纯问题、短句及无标点正文仍可返回；这些信号不等于语义相关性或答案质量。

`unavailable_body_matches` 是本次已枚举候选中正文未取得、未经验证或为空白的匹配数；`noncontent_body_matches` 是另一个互斥计数，表示正文已保存且非空，但去掉标签后没有文字或数字（包括纯标签、不可见字符及纯装饰符号）。默认模式在排序分页前将两类候选从 items 和 matched_count 排除，避免它们占用结果页。`content=all` 保留两类线索，并分别附上 `body_filter_reason=unavailable` 或 `no_text`；正常正文不附该字段。

过滤只判断是否有标签之外的文本，不设最短字数，也不因“见图”“视频”等词排除整篇；它不声称判断教程完整性或语义质量。原文、修订、固定版本读取和索引不变；搜索摘录也跳过纯标签或装饰段落。两个匹配计数都不是整库待处理数；`coverage` 描述范围内的保存状态，`body_available` 仍包含纯标签正文，不是有效文章数或召回率。必要条件约束整篇笔记，同篇出现多个城市时仍需 Agent 逐段归属。

read JSON：items（1–10 篇，每项指定 note_id/revision_id，可指定 block_ids 或 asset_ids）、mode（evidence/full/asset）、context、max_chars，或者 cursor 与 max_chars。同一请求不重复提交相同的 note_id/revision_id。context 仅适用于 evidence，必须为 0–4 的整数（布尔值无效），默认 1；指定每个锚点前后各返回的块数。相邻窗口合并去重并保持原文顺序，参数随游标保留，续页不能覆盖。

read 的 `data.items` **按笔记聚合**。每篇每页共享 note_id、revision_id、title、source_url、来源类型、内容范围和 gaps；evidence/full 模式返回 `blocks[]`（block_id、kind、原文 text、location），asset 模式返回 `assets[]`。不再为每段重复笔记元数据，不再返回单个 `block` / `asset` 或冗余 ordinal。数组保持原文顺序，证据 ID、内容和原始位置保留。

`continued` 表示本篇承接上一页，`has_more` 表示本篇的本次请求内容仍在下一页；它们不代表整篇或整夹已完整归档。`data.next_cursor` 指向本次请求的剩余结果，`data.errors` 保留逐项错误。`mode=evidence` 未指定块时以开头最多三个块为锚点，指定块时包含每个锚点及 context 范围的相邻块；全文核对使用 `full`。

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

备份及恢复目标必须为新目录。`exports/current.json` 指向完整阅读快照；备份包含数据库及引用对象，不含配对信息。阅读快照是派生数据：本机组件在资料停止变化 30 秒后刷新，持续写入时最多每 10 分钟刷新一次；重启后若 current.json 已对应当前 generation 则不重复导出。每次导出（含手动 `export`）只保留 current.json 指向的快照与最近共 3 份，并清理超过 1 小时的未完成 staging 快照；结果的 `pruned_snapshots` 为本次删除数。直接读文件时以 status 的 `readable_snapshot.behind` 判断是否落后。其他管理命令有 `sync pause --job ID`、`sync resume --job ID`、`sync abandon --job ID`（把未结束的列表扫描记为 interrupted）、`aliases list|add|remove`、`reindex`、`note delete ID` 和 `note restore ID`；删除是软删除。

## 独立图片获取

`images plan` 只读检查本地归档。指定 `--note ID`（可重复，最多 100 个）或 `--collection ID`，也可用 `--input` JSON。JSON 为 `note_ids` / `collection_id` 二选一，可传 `limit`（1–50，默认 8）与 `max_chars`（默认 6000）。续页仅传 `cursor` 和可选 `max_chars`。游标绑定 `local-image-plan-v2`、资料库 generation 和规划结果的摘要，结果变化时返回 `CURSOR_STALE`。

返回 `data.items` 中的固定笔记版本、来源、`media`、`body_image_hints`、`fetch_candidate`、`priority` 和已有 `gaps`。media 区分 deferred / unknown / partial / available / none_expected。读图、规划及任务进度共用本机文件校验。`local_file_check=size_and_sha256_when_known` 要求文件可读且非空，并校验已有的字节数和 SHA-256。旧归档按已有字段检查。`corrupt_local_files`、`unreadable_local_files`、`missing_local_files` 分别记录损坏、无法读取和路径对应文件缺失的数量。正文提示带原文块 ID 和字符位置，最多两条，否定句和问句保守地归为一般图片提及。

存在图片缺口且正文明确指向图片内容的候选标为 `explicit_body_reference`，其次为一般 `image_gap`，已解决者为 `already_resolved`。这是补图检查顺序，不承诺图片包含所需答案。`notes` / `fetch_candidates` / `explicit_reference_candidates` 是本次完整范围的计数；`access_restriction` 与 `acquisition_blocked` 表达当前停止保护。部分笔记不存在时保留其他成功条目和错误。plan 不返回下载链接；是否实际补图仍由独立 fetch 命令决定。

`images fetch --note ID` 支持重复指定笔记，`--collection ID` 处理该收藏夹的本地已归档成员。`--input request.json` 接收与 POST /v1/image-jobs 相同的请求，三类输入任选其一。CLI 和扩展通过共享入口创建任务，按笔记或收藏夹获取默认启用浏览器。已有完整清单时直接补下载，需要清单时等待浏览器读取。显式的 `browser:false` JSON 请求保留匿名读取方式，snapshot 请求直接使用传入的图片清单。

`--background` 仅排队。默认优先交给已运行本机组件，资料库空闲时先在前台处理可下载项。`images process [--job ID]` 处理已有清单的下载，等待图库的任务由已连接扩展的浏览器推进。pause / resume 需指定 job，resume 沿用原任务启用浏览器并保留已下载文件。同收藏夹的未结束任务会复用，显式 fetch 可继续其暂停状态或切换旧匿名任务。携带相同 job_id 的原请求重发仍按幂等规则返回原状态。

`images status --job ID` 默认返回 50 篇明细，可传 `--limit 1..100` 和 `--cursor CURSOR`。HTTP 与 Native Messaging 使用同一 GET 路径。`next_cursor` 绑定任务 ID、创建时间与上一页末尾的笔记 ID，支持进度变化期间续读，任务被替换时返回 CURSOR_STALE。汇总计数始终覆盖整个任务，逐项 `revision_id` 指向保存版本。`results_limited` 表示该页未包含所有任务条目，遍历以 `next_cursor` 是否为空为准。

浏览器 snapshot 包含 note_id、page_url、images（最多 100 个允许域名的 HTTPS 链接），以及可选 image_indices、expected_images、note_type。空 images 仅在 expected_images=0 有明确证据时接受。它只从详情的图片区域读取，不包含 Cookie。未知总数保持 null，不能把当前 DOM 图片数当作总数。

图片报告的 `images_saved` 按笔记图片位置合并已归档图片与逐张下载检查点，避免重计。`progress` 分开给出 waiting_gallery / reading_gallery / waiting_download / downloading，以及当前笔记已保存与预期图片数。`needs_repair` 表示 ready 项中的本地文件已缺失或未通过校验，此时 complete=false，显式继续可利用已有完整清单修复。

新建任务先略过本地已齐全的笔记。浏览器读取每个图片任务最多积压 2 份待下载目录，正在下载也计入，其他任务仍可调度。图库支持 XHS fraction 分页和有界后台翻图；前台用户页不翻动。跨次 partial 仅在可靠序号、总数、正文上下文一致且文件校验通过时合并；已知图片版本冲突会保守保留缺口。

CLI 与扩展的普通补图都使用 browser_mode / browser_pending。待浏览器清单的条目由浏览器租约处理，收到有效清单后再下载图片。不完整清单先保存已取得图片并标记 partial，显式继续时更新清单。正文和图片页使用同一单页读取保护，并按 browser_last_kind 交替调度。

显式继续产生的图片租约带 `resumed` 标记。扩展在本机确认租约仍有效、待发送队列中没有该任务的暂停请求、且本地取消代次保持一致时，可清除已确认的旧暂停标记，从而接受 CLI/API 发起的继续。新的暂停和访问限制优先生效。

单独补图只发布图片相关变化，保留正文和原始正文快照。每张图片完成后保存检查点，发布前核对笔记版本和 worker 租约；并发正文更新返回 REVISION_CONFLICT，切换读取方式或取消后的旧 worker 不能覆盖新任务。失败刷新保留旧的可用版本。正文后续更新保留单独取得的图片，并通过 IMAGES_CAPTURED_SEPARATELY 说明并非同次页面快照。

`read mode=asset` 返回当前本地文件对应的 media_state、该版本的 expected_images 和 assets。文件通过校验后才返回 path。逐图错误包括 ASSET_MISSING、ASSET_CORRUPT、ASSET_UNREADABLE 和 INVALID_ASSET_PATH，其他可读图片保留。MEDIA_DEFERRED 表示尚未获取，IMAGE_COUNT_UNKNOWN 表示总数未知，IMAGE_LIST_INCOMPLETE / IMAGE_LIST_UNVERIFIED 表示清单不全或未确认。已确认无图且未指定 asset_id 时返回完整空列表。补图成功后可按新 revision_id 阅读，旧版本仍可引用。

`search --match phrase` / JSON `match=phrase` 要求每条完整查询在标题、正文或已有 OCR 的同一行连续出现，忽略大小写、全半角和横向空白差异，沿用等价别名；标签不能单独满足。默认 terms 不变，短语模式会遗漏改写。游标保留模式，续页仍只能附加 max_chars。

## 已知实现限制

- 页面适配器使用收藏卡片和笔记正文 DOM，以及常见列表终点元素；它不监听私有接口、不读取 Cookie。网站改版、缺失或过期链接仍可能造成正文缺口。大收藏列表的全量覆盖仍需单独核对。
- 平台文件夹 ID 未作普遍验证，扩展按页面地址和可见视图标记保存 local_view 身份。自动识别名称不等于确认原平台文件夹 ID；名称与总数可手动修正。
- 匿名页面解析只接受可解析的序列化初始状态，不执行网页 JavaScript。只有识别到笔记详情结构才将正文标记为已取得；仅命中信息流预览时记录 BODY_UNVERIFIED，不计为正文完成。没有支持的状态时失败，保留上一个可用版本。这种结构判断仍需真实页面人工核对。
- 图片获取已在隔离夹具通过，真实网站图片区域适配未验收；懒加载图片可能不全，下载的是页面提供版本，不保证最高分辨率。新下载的图片不运行 OCR，因此不会自动进入文字检索；视频仍未实现。
- 确认停止增长不代表完整；扩展等待用户核对。暂停时允许当前网络请求结束，但不再发布尚未完成的处理结果。
- 每次新扫描都会重新尝试已枚举笔记的正文读取。版本和对象按哈希去重，刷新频率策略尚未优化。
- 搜索是中文分词、字面匹配、少量别名与相关性融合；没有 embedding 或重排模型。
- 本地删除是软删除；尚未提供历史版本和对象的物理清理功能。
- 安装脚本、锁文件和源码针对 Windows 原型交付；没有声称其他操作系统已经验收。
- Google Maps 保存、真实收藏效果评测、Harness 接入不包含在已完成的本地自动化测试中。

## 代码来源

本原型参考计划书中的公开资料与页面数据格式独立实现，没有复制“看看收藏”的实现文件。上游项目的许可与固定提交见研究记录；未来若直接移植代码，应另行记录具体来源和许可。

