# 检索与回读参数

下面的 PowerShell 示例用 `$xhs` 表示本 Skill 内 `scripts/xhs.ps1` 的绝对路径。

## 限定搜索

```powershell
& $xhs search --collection COLLECTION_ID --query "张量并行" --match phrase
& $xhs search --query "Madrid tapas" --query "马德里 小吃" --require "马德里"
```

- `--collection` 接受本地收藏夹 ID，可重复。`--query` 可传 1–4 次，结果按笔记合并。每个 `--require` 都适用于所有候选，标签本身无法满足必要条件。
- 默认 `terms` 允许同篇不同段落分别命中关键词，也用于按标签查找笔记。完整命中排在部分命中之前。`matched_count` 只计完整命中，`partial_match[]` 中的 `missing_terms` 列出缺词。`phrase` 用于核对写法，要求短语连续出现在同一字段的一行内，可能漏掉改写表达。
- `effective_queries` 显示本轮展开的等价名称。`aliases list` 可查看本库的别名组。尚未收录的别称可作为另一个查询，相关主题是否纳入由问题和原文决定。
- 默认 `--content body` 要求已存正文在去掉标签后仍有文字或数字。`--content all` 也返回只有标题、标签或既有 OCR 等线索的候选。可用 `matched_fields` 核对命中位置。`coverage` 统计所选范围的归档情况，`body_available` 计已保存的正文，包含纯标签正文。

## 按固定版本回读

从搜索结果复制 ID，把下面的请求保存为 UTF-8 JSON，执行 `& $xhs read --input read.json`。一次可读 1–10 篇。

```json
{
  "items": [
    {"note_id": "NOTE_ID", "revision_id": "REVISION_ID", "block_ids": ["BLOCK_ID"]}
  ],
  "mode": "evidence",
  "context": 1
}
```

`evidence` 返回所选块及前后各 0–4 个块，默认各 1 个。读全文时用 `"mode": "full"` 并省去 `block_ids` 和 `context`。读图时用 `"mode": "asset"`，每项保留笔记和版本 ID。结果按篇放在 `data.items[]`，正文看 `blocks[]`，图片看 `assets[]` 中带 `path` 的项。引用可用 `revision_id` 配合 `block_id` 或 `asset_id`。来源标记为 `synthetic_fixture` 的内容只适用于明确指定的样例任务。

用户专门询问图片归档情况时，`images plan --note NOTE_ID` 或 `images plan --collection COLLECTION_ID` 可只读查看项目记录的缺口。已保存图片中的文字以现有 OCR 结果参与文本检索，取得图片后可通过宿主看图工具直接阅读。

## 续页与部分结果

- `data.next_cursor` 有值且仍需要后续内容时，搜索用 `& $xhs search --cursor CURSOR`。回读用 `& $xhs read --input next.json`，文件内容为 `{"cursor":"CURSOR"}`。续页只接受游标和可选的 `max_chars`，后者限定整个 JSON 响应的字符数。
- 退出码 `3` 表示部分成功。保留已返回的材料，结合 `warnings`、`data.errors` 和各篇 `gaps` 判断哪些结论仍有依据。候选数量触及上限时，搜索结果可能有遗漏。
- `CURSOR_STALE` 需要重发原始请求。`OUTPUT_BUDGET_TOO_SMALL` 可提高 `max_chars` 或缩小单次请求。检查当前归档范围用 `status`。
