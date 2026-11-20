"""Local, deterministic ranking and source-preserving excerpts; no model calls."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from .text import normalize, query_groups, tokens

SEARCH_VERSION = "lexical-body-v3"
READ_VERSION = "grouped-evidence-v2"
HASHTAG = re.compile(r"(?<![A-Za-z0-9_+])[#＃][\w\u3400-\u9fff]+", re.UNICODE)


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", normalize(value)) if not unicodedata.combining(c))


def without_tags(value):
    return HASHTAG.sub(" ", value)


def has_body_text(value):
    """Ignore tags and decoration without imposing a minimum length on real text."""
    return any(char.isalnum() for char in without_tags(value))


@lru_cache(maxsize=1024)
def terms_for(query):
    words = tokens(query)
    # cut_for_search emits overlapping Chinese subwords. Keep the complete word.
    return [word for word in words if not (re.fullmatch(r"[\u3400-\u9fff]+", word) and
            any(word != other and word in other for other in words))]


def term_pattern(term):
    if re.fullmatch(r"[a-z0-9][a-z0-9_+#./-]*", term):
        return r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z_+#])" if term.endswith(("+", "#")) else (
            r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_+#])")
    return re.escape(term)


def present(term, folded_text):
    return bool(re.search(term_pattern(term), folded_text))


def document_fields(row, payload):
    body = payload.get("body", "")
    ocr = "\n".join(line.get("text", "") for asset in payload.get("images", []) for line in asset.get("ocr_lines", []))
    return {"title": fold(without_tags(row["title"])), "body": fold(without_tags(body)),
            "ocr": fold(without_tags(ocr)),
            "tags": fold(" ".join(HASHTAG.findall(row["title"] + "\n" + body + "\n" + ocr)))}


def required_match(fields, required_terms):
    substantive = "\n".join(fields[key] for key in ("title", "body", "ocr"))
    return all(any(all(present(word, substantive) for word in terms_for(query))
                   for group in query_groups([required]) for query in group) for required in required_terms)


def rank_document(fields, groups, fts_ranks):
    scored, matched_queries, matched_fields = [], [], set()
    for group in groups:
        best = 0
        for query in group:
            words = terms_for(query)
            if not words:
                continue
            hits = {key: [present(word, value) for word in words] for key, value in fields.items()}
            if not all(any(hits[key][i] for key in hits) for i in range(len(words))):
                continue
            meaningful = all(any(hits[key][i] for key in ("title", "body", "ocr")) for i in range(len(words)))
            # Tags can discover a lead, but cannot outrank an explanation in the body.
            score = 4.0 if meaningful else 0.2
            score += sum(hits["title"]) / len(words) * 1.0
            score += sum(hits["body"]) / len(words) * 0.5
            score += sum(hits["ocr"]) / len(words) * 0.3
            if fold(query) in fields["title"]:
                score += 1.0
            rank = fts_ranks.get(query)
            if rank is not None:
                score += 60 / (60 + rank)
            best = max(best, score)
            matched_queries.append(query)
            matched_fields.update(key for key, flags in hits.items() if any(flags))
        if best:
            scored.append(best)
    # Reward independent topic matches slightly, never add a fixed bonus per alias.
    return (max(scored) + min(len(scored) - 1, 2) * .15 if scored else 0,
            sorted(set(matched_queries)), sorted(matched_fields))


def original_spans(text, terms):
    folded, positions = [], []
    for index, char in enumerate(text):
        for value in fold(char):
            folded.append(value)
            positions.append(index)
    value = "".join(folded)
    spans = set()
    for term in terms:
        for match in re.finditer(term_pattern(term), value):
            if match.end() > match.start():
                spans.add((positions[match.start()], positions[match.end() - 1] + 1))
    return sorted(spans)


def excerpts(blocks, queries, required_terms, maximum=300):
    terms = set(word for q in queries for word in terms_for(q))
    anchors = set(word for group in query_groups(required_terms) for q in group for word in terms_for(q))
    scored = []
    for block in blocks:
        text = block["text"]
        meaningful = without_tags(text)
        if not has_body_text(text):
            continue
        value = fold(meaningful)
        hits = sum(present(term, value) for term in terms)
        anchor_hits = sum(present(term, value) for term in anchors)
        scored.append((-(hits + 2 * anchor_hits), block["ordinal"], block, hits + anchor_hits > 0))
    scored.sort(key=lambda entry: entry[:2])
    results = []
    selected = [entry for entry in scored if entry[3]][:2] or scored[:1]
    for _, _, block, matched in selected:
        text = block["text"]
        spans = original_spans(text, terms | anchors) if matched else []
        start = 0
        if spans and len(text) > maximum:
            # Prefer a window containing several hits, with space before the first.
            best = max(spans, key=lambda span: sum(span[0] <= other[0] < span[0] + maximum - 60 for other in spans))
            start = min(max(0, best[0] - 60), len(text) - maximum)
        end = min(len(text), start + maximum)
        location = json.loads(block["location"])
        if block["kind"] == "body":
            location = {"start": location["start"] + start, "end": location["start"] + end}
        results.append({"block_id": block["block_id"], "kind": block["kind"], "text": text[start:end],
                        "location": location, "excerpt_only": start > 0 or end < len(text), "matched": matched})
    return results


def group_evidence(units, *, continued_key=None, next_key=None):
    grouped = {}
    for unit in units:
        key = (unit["note_id"], unit["revision_id"])
        if key not in grouped:
            common = {k: v for k, v in unit.items() if k not in {"block", "asset"}}
            common["assets" if "asset" in unit else "blocks"] = []
            common["continued"] = key == continued_key
            common["has_more"] = key == next_key
            grouped[key] = common
        if unit.get("block") is not None:
            grouped[key]["blocks"].append({k: v for k, v in unit["block"].items() if k != "ordinal"})
        if "asset" in unit:
            grouped[key]["assets"].append(unit["asset"])
    return list(grouped.values())
