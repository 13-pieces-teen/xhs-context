"""Local, deterministic ranking and source-preserving excerpts; no model calls."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from .text import normalize, query_groups, tokens

SEARCH_VERSION = "lexical-body-v6"
READ_VERSION = "grouped-evidence-v4"
HASHTAG = re.compile(r"(?<![A-Za-z0-9_+])[#＃][\w\u3400-\u9fff]+", re.UNICODE)


_MAYBE_COMBINING = re.compile(r"[^\x00-\x7f\u3400-\u4dbf\u4e00-\u9fff]")


def fold(value):
    # ASCII and CJK ideographs are never combining marks; only inspect the rest.
    return _MAYBE_COMBINING.sub(lambda m: "" if unicodedata.combining(m[0]) else m[0],
                                unicodedata.normalize("NFKD", normalize(value)))


def without_tags(value):
    return HASHTAG.sub(" ", value)


def has_body_text(value):
    """Ignore tags and decoration without imposing a minimum length on real text."""
    return any(char.isalnum() for char in without_tags(value))


@lru_cache(maxsize=1024)
def terms_for(query):
    # A sentence-final "." or "/" belongs to the query text, not to an identifier such as k8s.
    words = list(dict.fromkeys(word.rstrip("./-") if word[0].isascii() else word for word in tokens(query)))
    # cut_for_search emits overlapping Chinese subwords. Keep the complete word.
    return [word for word in words if word and not (re.fullmatch(r"[\u3400-\u9fff]+", word) and
            any(word != other and word in other for other in words))]


def literal_term(value):
    """Trim surrounding punctuation from a literal; keep # and + that identify C#, C++ or tags."""
    def edge(char):
        return char != "#" and unicodedata.category(char).startswith("P")
    start, end = 0, len(value)
    while start < end and edge(value[start]):
        start += 1
    while end > start and edge(value[end - 1]):
        end -= 1
    return value[start:end]


def term_pattern(term):
    if re.fullmatch(r"[a-z0-9][a-z0-9_+#./-]*", term):
        return r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z_+#])" if term.endswith(("+", "#")) else (
            r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_+#])")
    return re.escape(term)


@lru_cache(maxsize=4096)
def compiled_term(term):
    return re.compile(term_pattern(term))


def present(term, folded_text):
    return compiled_term(term).search(folded_text) is not None


def document_fields(row, payload):
    body = payload.get("body", "")
    ocr = "\n".join(line.get("text", "") for asset in payload.get("images", []) for line in asset.get("ocr_lines", []))
    return {"title": fold(without_tags(row["title"])), "body": fold(without_tags(body)),
            "ocr": fold(without_tags(ocr)),
            "tags": fold(" ".join(HASHTAG.findall(row["title"] + "\n" + body + "\n" + ocr)))}


def required_match(fields, required_terms, aliases=None):
    substantive = "\n".join(fields[key] for key in ("title", "body", "ocr"))
    return all(any(all(present(word, substantive) for word in terms_for(query))
                   for group in query_groups([required], aliases) for query in group) for required in required_terms)


def phrase_present(query, value):
    # Preserve line/field boundaries and Latin word boundaries; normalize horizontal space only.
    phrase = re.sub(r"[^\S\r\n]+", " ", fold(query)).strip()
    if not phrase or "\n" in phrase or "\r" in phrase:
        return False
    left = r"(?<![a-z0-9_])" if re.match(r"[a-z0-9]", phrase) else ""
    right = r"(?![a-z0-9_+#])" if re.search(r"[a-z0-9+#]$", phrase) else ""
    return bool(re.search(left + re.escape(phrase) + right, re.sub(r"[^\S\r\n]+", " ", value)))


def rank_document(fields, groups, fts_ranks, match="terms"):
    scored, matched_queries, matched_fields = [], [], set()
    for group in groups:
        best = 0
        for query in group:
            words = terms_for(query)
            if not words:
                continue
            phrase_fields = {key for key in ("title", "body", "ocr") if phrase_present(query, fields[key])} if match == "phrase" else set()
            if match == "phrase" and not phrase_fields:
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
            matched_fields.update(phrase_fields if match == "phrase" else (key for key, flags in hits.items() if any(flags)))
        if best:
            scored.append(best)
    # Reward independent topic matches slightly, never add a fixed bonus per alias.
    return (max(scored) + min(len(scored) - 1, 2) * .15 if scored else 0,
            sorted(set(matched_queries)), sorted(matched_fields))


def partial_rank(fields, groups):
    """A weaker tier for notes that miss some words of a multi-word query.

    Only title, body and OCR hits count; at least half of the words must be present,
    and single CJK characters alone cannot qualify a note. Callers rank this tier
    strictly after every note that matches all words of some query.
    """
    scored, matched_queries, matched_fields, missing = [], [], set(), []
    for group in groups:
        best = None
        for query in group:
            words = terms_for(query)
            if len(words) < 2:
                continue
            hits = {key: [present(word, fields[key]) for word in words] for key in ("title", "body", "ocr")}
            found = [i for i in range(len(words)) if any(hits[key][i] for key in hits)]
            substantive = [i for i in found if not re.fullmatch(r"[\u3400-\u9fff]", words[i])]
            if not substantive or len(found) * 2 < len(words) or len(found) == len(words):
                continue
            score = len(found) / len(words) * 2.0
            score += sum(hits["title"]) / len(words) * .5 + sum(hits["body"]) / len(words) * .25
            score += sum(hits["ocr"]) / len(words) * .15
            if best is None or score > best[0]:
                best = (score, query, {key for key, flags in hits.items() if any(flags)},
                        [words[i] for i in range(len(words)) if i not in found])
        if best:
            scored.append(best[0])
            matched_queries.append(best[1])
            matched_fields.update(best[2])
            missing.append({"query": best[1], "missing_terms": best[3]})
    if not scored:
        return 0, [], [], []
    return (max(scored) + min(len(scored) - 1, 2) * .15, sorted(set(matched_queries)),
            sorted(matched_fields), missing)


def original_spans(text, terms):
    folded, positions = [], []
    for index, char in enumerate(text):
        for value in fold(char):
            folded.append(value)
            positions.append(index)
    value = "".join(folded)
    spans = set()
    for term in terms:
        for match in compiled_term(term).finditer(value):
            if match.end() > match.start():
                spans.add((positions[match.start()], positions[match.end() - 1] + 1))
    return sorted(spans)


def excerpts(blocks, queries, required_terms, maximum=300, *, title='', match='terms', aliases=None):
    terms = set(word for q in queries for word in terms_for(q))
    anchors = set(word for group in query_groups(required_terms, aliases) for q in group for word in terms_for(q))
    scored = []
    for block in blocks:
        text = block["text"]
        meaningful = without_tags(text)
        if not has_body_text(text):
            continue
        value = fold(meaningful)
        hits = sum(present(term, value) for term in terms)
        anchor_hits = sum(present(term, value) for term in anchors)
        # Resolve otherwise equal lexical matches with observable structure,
        # not a domain-specific definition of "good content". Questions remain
        # eligible; a question followed by an explanation can win this tie.
        repeated_title = bool(title) and value.strip() == fold(without_tags(title)).strip()
        unnumbered = re.sub(r'^\s*(?:\d+[.)、]|[-*])\s*', '', meaningful)
        sentence = bool(re.search(r'。|(?<!\d)[.!](?:\s|$)', unnumbered.strip()))
        phrase_hit = any(phrase_present(query, value) for query in queries)
        tie = int(phrase_hit) if match == 'phrase' else 0
        tie += .5 * int(sentence) - int(repeated_title)
        scored.append((-(hits + 2 * anchor_hits), -tie, block["ordinal"], block, hits + anchor_hits > 0))
    scored.sort(key=lambda entry: entry[:3])
    results = []
    selected = [entry for entry in scored if entry[4]][:2] or scored[:1]
    for _, _, _, block, matched in selected:
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
        if unit.get("asset") is not None:
            grouped[key]["assets"].append(unit["asset"])
    return list(grouped.values())
