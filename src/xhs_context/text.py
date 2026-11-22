from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache

TOKENIZER_VERSION = "jieba-0.42.1+identifiers-v1"
# Built-in equivalent names only. Topic expansion belongs to the calling Agent;
# libraries can add more equivalents with `xhs-context aliases add`.
ALIASES = {"madrid": ("madrid", "马德里"), "kubernetes": ("kubernetes", "k8s"),
           "infra": ("infra", "基础设施"), "ai infra": ("ai infra", "aiinfra", "ai 基础设施")}


def normalize(value):
    return unicodedata.normalize("NFKC", value).casefold()


@lru_cache(maxsize=1)
def tokenizer():
    import jieba
    jieba.setLogLevel(logging.ERROR)
    instance = jieba.Tokenizer()
    instance.initialize()
    for term in ["马德里", "土豆饼", "可观测性", "基础设施", "云原生", "向量数据库"]:
        instance.add_word(term)
    return instance


def tokens(value):
    result = []
    folded = "".join(c for c in unicodedata.normalize("NFKD", normalize(value)) if not unicodedata.combining(c))
    for piece in re.findall(r"[a-z0-9][a-z0-9_+#./-]*|[\u3400-\u9fff]+", folded):
        if re.fullmatch(r"[\u3400-\u9fff]+", piece):
            result.extend(tokenizer().cut_for_search(piece))
        else:
            result.append(piece)
    return list(dict.fromkeys(t for t in result if t.strip()))


def indexed(value):
    return " ".join(tokens(value))


def alias_pattern(value):
    return (r"(?<![a-z0-9_])" if value[0].isascii() else "") + re.escape(value) + (
        r"(?![a-z0-9_])" if value[-1].isascii() else "")


def spelled(value):
    return " ".join(normalize(value).split())


@lru_cache(maxsize=32)
def _alias_index(items):
    table = dict(items)
    names = {name: canonical for canonical, variants in items for name in variants}
    pattern = re.compile("|".join(alias_pattern(name) for name in sorted(names, key=len, reverse=True)))
    return table, names, pattern


def query_groups(queries, aliases=None):
    """Deduplicate equivalent reformulations so they do not inflate a note's score.

    The caller's own spelling leads each group; the other names follow.
    """
    table, names, pattern = _alias_index(tuple(sorted(
        (canonical, tuple(variants)) for canonical, variants in (aliases or ALIASES).items())))
    groups = {}
    for query in queries:
        original = spelled(query)
        canonical = pattern.sub(lambda m: names[m[0]], original)
        if canonical in groups:
            continue
        variants, end = [""], 0
        for match in pattern.finditer(canonical):
            variants = [prefix + canonical[end:match.start()] + alias
                        for prefix in variants for alias in table[names[match[0]]]][:8]
            end = match.end()
        variants = list(dict.fromkeys(v + canonical[end:] for v in variants))
        if original in variants:
            variants.remove(original)
            variants.insert(0, original)
        groups[canonical] = variants
    return list(groups.values())


def expanded_queries(queries, aliases=None):
    return list(dict.fromkeys(q for group in query_groups(queries, aliases) for q in group))


def body_blocks(body, max_length=1200):
    """Preserve every character and its original offset; split long paragraphs explicitly."""
    offset = 0
    for paragraph in body.splitlines(keepends=True):
        for start in range(0, len(paragraph), max_length):
            text = paragraph[start:start + max_length]
            if text.strip():
                yield {"kind": "body", "text": text,
                       "location": {"start": offset + start, "end": offset + start + len(text),
                                    "continued": start > 0 or len(paragraph) > max_length}}
        offset += len(paragraph)


def evidence_blocks(note):
    blocks = list(body_blocks(note.get("body", "")))
    for image_index, asset in enumerate(note.get("images", []), 1):
        for line in asset.get("ocr_lines", []):
            if line.get("text", "").strip():
                blocks.append({"kind": "ocr", "text": line["text"],
                               "location": {"image_index": image_index, "asset_id": asset["asset_id"],
                                            "box": line.get("box"), "confidence": line.get("confidence")}})
    for i, block in enumerate(blocks):
        block["block_id"], block["ordinal"] = f"b{i + 1:05d}", i
    return blocks

