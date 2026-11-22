from __future__ import annotations

import json
import re

from .common import (XhsError, bounded_page, decode_cursor, encode_cursor, envelope,
                     fields, integer, safe_id, text_field)
from .jobs import job_report
from .access import access_restriction
from .text import TOKENIZER_VERSION, normalize, query_groups
from .retrieval import (SEARCH_VERSION, READ_VERSION, terms_for, document_fields, required_match,
                        rank_document, partial_rank, literal_term, excerpts, group_evidence, has_body_text)


def scope_clause(collections):
    if not collections:
        return "", []
    return (" AND EXISTS (SELECT 1 FROM memberships m WHERE m.note_id=n.id AND "
            "m.state<>'removed' AND m.collection_id IN (" + ",".join("?" for _ in collections) + "))",
            collections)


def coverage(db, collections=()):
    clause, params = scope_clause(collections)
    row = db.execute(
        "SELECT count(*),sum(CASE WHEN json_extract(r.payload,'$.content_state')='complete' THEN 1 ELSE 0 END),"
        "sum(CASE WHEN json_extract(r.payload,'$.content_state')='metadata_only' THEN 1 ELSE 0 END),"
        "sum(CASE WHEN json_extract(r.payload,'$.body_state')='available' THEN 1 ELSE 0 END),"
        "sum(CASE WHEN json_extract(r.payload,'$.media_state')='deferred' THEN 1 ELSE 0 END) "
        "FROM notes n LEFT JOIN revisions r ON r.id=n.current_revision WHERE n.deleted=0" + clause, params).fetchone()
    scoped = ""
    if collections:
        scoped = " WHERE c.id IN (" + ",".join("?" for _ in collections) + ")"
    folders = [dict(r) for r in db.execute(
        "SELECT c.id,c.name,j.scan_state,j.updated_at FROM collections c LEFT JOIN jobs j ON j.id=c.last_job" +
        scoped + " ORDER BY c.id", list(collections))]
    return {"notes": row[0], "content_complete": row[1] or 0, "metadata_only": row[2] or 0,
            "body_available": row[3] or 0, "media_deferred": row[4] or 0,
            "collections": folders, "scope": "archived_including_former_favorites" if not collections else "collection_members"}


def status(store, job_id=None):
    from .image_jobs import image_reports
    with store.read() as db:
        data = {"library": str(store.root), "generation": store.generation(db), "coverage": coverage(db),
                "access_restriction": access_restriction(db)}
        data['image_jobs'] = image_reports(db)
        if job_id:
            data["job"] = job_report(db, job_id)
        else:
            data["recent_jobs"] = [job_report(db, r[0]) for r in db.execute(
                "SELECT id FROM jobs ORDER BY created_at DESC LIMIT 5").fetchall()]
        pointer = store.root / "exports/current.json"
        if pointer.exists():
            try:
                snapshot = json.loads(pointer.read_text(encoding="utf-8"))
                data["readable_snapshot"] = dict(snapshot, behind=snapshot["generation"] != data["generation"])
            except (ValueError, KeyError):
                data["readable_snapshot"] = {"state": "INVALID_POINTER"}
        else:
            data["readable_snapshot"] = {"state": "EXPORT_PENDING"}
    return envelope(data)


def validate_search(request):
    fields(request, {"queries", "collection_ids", "limit", "max_chars", "cursor", "content", "required_terms", "match"})
    if request.get("cursor"):
        cursor = decode_cursor(request["cursor"], "search")
        if cursor.get("search_version") != SEARCH_VERSION:
            raise XhsError("CURSOR_STALE", "Search strategy changed; repeat the original search")
        if any(k in request for k in ["queries", "collection_ids", "limit", "content", "required_terms", "match"]):
            raise XhsError("INVALID_ARGUMENT", "Use cursor with max_chars only", exit_code=2)
        base = validate_search(cursor.get("request", {}))[0]
        integer(cursor.get("offset"), "cursor offset", 0, 100000)
        integer(cursor.get("generation"), "cursor generation", 0, 2**63 - 1)
        return base, cursor, request.get("max_chars", 6000)
    queries = request.get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 4:
        raise XhsError("INVALID_ARGUMENT", "Provide 1-4 queries", exit_code=2)
    for query in queries:
        text_field(query, "query", 300, False)
    collections = request.get("collection_ids", [])
    if not isinstance(collections, list) or len(collections) > 20:
        raise XhsError("INVALID_ARGUMENT", "collection_ids must contain at most 20 IDs", exit_code=2)
    for item in collections:
        safe_id(item, "collection_id")
    limit = integer(request.get("limit", 8), "limit", 1, 50)
    content = request.get("content", "body")
    if not isinstance(content, str) or content not in {"body", "all"}:
        raise XhsError("INVALID_ARGUMENT", "content must be body or all", exit_code=2)
    match = request.get("match", "terms")
    if not isinstance(match, str) or match not in {"terms", "phrase"}:
        raise XhsError("INVALID_ARGUMENT", "match must be terms or phrase", exit_code=2)
    required = request.get("required_terms", [])
    if not isinstance(required, list) or len(required) > 4:
        raise XhsError("INVALID_ARGUMENT", "At most 4 required terms are accepted", exit_code=2)
    for term in required:
        text_field(term, "required term", 100, False)
        if not terms_for(term):
            raise XhsError("INVALID_ARGUMENT", "A required term must contain searchable text", exit_code=2)
    return {"queries": queries, "collection_ids": list(dict.fromkeys(collections)), "limit": limit,
            "content": content, "match": match, "required_terms": list(dict.fromkeys(required))}, None, request.get("max_chars", 6000)


def search(store, request):
    from .aliases import effective_aliases
    base, cursor, budget = validate_search(request)
    scope, scope_params = scope_clause(base["collection_ids"])
    with store.read() as db:
        if db.execute("SELECT value FROM meta WHERE key='tokenizer_version'").fetchone()[0] != TOKENIZER_VERSION:
            raise XhsError("INDEX_REBUILD_REQUIRED", "Run reindex after changing tokenizer configuration")
        generation = store.generation(db)
        if cursor and cursor["generation"] != generation:
            raise XhsError("CURSOR_STALE", "The index changed; repeat the original search")
        aliases = effective_aliases(db)
        groups = query_groups(base["queries"], aliases)
        expanded = list(dict.fromkeys(q for group in groups for q in group))
        for folder in base["collection_ids"]:
            if not db.execute("SELECT 1 FROM collections WHERE id=?", (folder,)).fetchone():
                raise XhsError("COLLECTION_NOT_FOUND", f"Unknown collection: {folder}")
        candidates, partial_candidates, fts_ranks = set(), set(), {}
        candidate_limit_reached = partial_limit_reached = False
        for query in expanded:
            words = terms_for(query)
            rows = []
            # Symbols are meaningful in identifiers; don't turn C++ into a generic C query.
            symbols = bool(re.search(r"[a-z0-9][+#]", normalize(query)))
            quoted = ['"' + token.replace('"', '""') + '"' for token in words]
            if words and not symbols:
                rows = db.execute(
                    "SELECT n.id,bm25(note_fts,0,5,1,0.8) AS score FROM note_fts "
                    "JOIN notes n ON n.id=note_fts.note_id WHERE note_fts MATCH ? AND n.deleted=0" +
                    scope + " ORDER BY score,n.id LIMIT 1001", [" AND ".join(quoted), *scope_params]).fetchall()
                candidate_limit_reached |= len(rows) > 1000
                fts_ranks[query] = {row["id"]: rank for rank, row in enumerate(rows[:1000])}
                candidates.update(fts_ranks[query])
            literals = [term for term in (literal_term(t) for t in normalize(query).split()) if term]
            if literals:
                condition = " AND ".join("instr(d.normalized,?)>0" for _ in literals)
                literal_rows = db.execute(
                    "SELECT n.id,n.title FROM notes n JOIN search_docs d ON d.note_id=n.id "
                    "WHERE n.deleted=0 AND " + condition + scope + " ORDER BY n.id LIMIT 1001",
                    [*literals, *scope_params]).fetchall()
                candidate_limit_reached |= len(literal_rows) > 1000
                candidates.update(row["id"] for row in literal_rows[:1000])
            if base["match"] == "terms" and len(words) > 1:
                # Notes missing some words form a lower tier; full matches keep their order.
                if not symbols:
                    rows = db.execute(
                        "SELECT n.id FROM note_fts JOIN notes n ON n.id=note_fts.note_id "
                        "WHERE note_fts MATCH ? AND n.deleted=0" + scope + " ORDER BY bm25(note_fts,0,5,1,0.8),n.id LIMIT 1001",
                        [" OR ".join(quoted), *scope_params]).fetchall()
                    partial_limit_reached |= len(rows) > 1000
                    partial_candidates.update(row["id"] for row in rows[:1000])
                if len(literals) > 1:
                    rows = db.execute(
                        "SELECT n.id FROM notes n JOIN search_docs d ON d.note_id=n.id WHERE n.deleted=0 AND (" +
                        " OR ".join("instr(d.normalized,?)>0" for _ in literals) + ")" + scope +
                        " ORDER BY n.id LIMIT 1001", [*literals, *scope_params]).fetchall()
                    partial_limit_reached |= len(rows) > 1000
                    partial_candidates.update(row["id"] for row in rows[:1000])
        scores, matches, field_matches, records, body_filters = {}, {}, {}, {}, {}
        tiers, partial_details = {}, {}
        metadata_matches, noncontent_matches = 0, 0
        # Rank source fields without changing the immutable original or its index.
        for note_id in sorted(candidates | partial_candidates):
            row = db.execute("SELECT n.*,r.payload FROM notes n JOIN revisions r ON r.id=n.current_revision WHERE n.id=?",
                             (note_id,)).fetchone()
            payload = json.loads(row["payload"])
            values = document_fields(row, payload)
            if not required_match(values, base["required_terms"], aliases):
                continue
            score, matched, matched_fields = rank_document(values, groups, {
                q: ranks[note_id] for q, ranks in fts_ranks.items() if note_id in ranks}, base["match"])
            tier = 0
            if not score and base["match"] == "terms":
                score, matched, matched_fields, missing = partial_rank(values, groups)
                tier = 1
            if not score:
                continue
            has_body = payload.get("body_state") == "available" and bool(payload.get("body", "").strip())
            reason = None
            if not has_body:
                reason = "unavailable"
            elif not has_body_text(payload["body"]):
                reason = "no_text"
            if reason:
                body_filters[note_id] = reason
                if tier == 0:
                    metadata_matches += reason == "unavailable"
                    noncontent_matches += reason == "no_text"
            if base["content"] == "body" and note_id in body_filters:
                continue
            scores[note_id], matches[note_id], field_matches[note_id] = score, matched, matched_fields
            records[note_id], tiers[note_id] = (row, payload), tier
            if tier:
                partial_details[note_id] = missing
        ordered = sorted(scores, key=lambda key: (tiers[key], -scores[key], key))
        full_count = sum(1 for key in ordered if not tiers[key])
        offset = cursor["offset"] if cursor else 0
        integer(offset, "cursor offset", 0, len(ordered))
        units = []
        for note_id in ordered[offset:offset + base["limit"]]:
            row, payload = records[note_id]
            blocks = [dict(b) for b in db.execute(
                "SELECT block_id,kind,text,location,ordinal FROM evidence_blocks WHERE revision_id=?",
                (row["current_revision"],))]
            snippets = excerpts(blocks, matches[note_id], base["required_terms"], title=row['title'],
                                match=base['match'], aliases=aliases)
            units.append({"note_id": note_id, "revision_id": row["current_revision"], "title": row["title"],
                          "source_url": payload.get("canonical_url", row["source_url"].split("?")[0]),
                          "source_kind": payload.get("source_kind", "xiaohongshu"),
                          "matched_queries": matches[note_id], "matched_fields": field_matches[note_id], "snippets": snippets,
                          "content_state": payload.get("content_state", "unknown"),
                          "content_scope": payload.get("content_scope", "archive"),
                          "gaps": payload.get("gaps", [])})
            if note_id in body_filters:
                units[-1]["body_filter_reason"] = body_filters[note_id]
            if tiers[note_id]:
                units[-1]["partial_match"] = partial_details[note_id]
        cov = coverage(db, base["collection_ids"])
        warnings = []
        if candidate_limit_reached:
            warnings.append({"code": "CANDIDATES_LIMITED", "message": "Narrow the query; some candidates were not ranked"})
        if partial_limit_reached:
            warnings.append({"code": "PARTIAL_CANDIDATES_LIMITED",
                             "message": "Some notes missing query words were not ranked; full matches are unaffected"})
        def cursor_at(index):
            return encode_cursor({"v": 1, "kind": "search", "search_version": SEARCH_VERSION, "generation": generation,
                                  "request": base, "offset": offset + index})
        def response(items, next_cursor):
            # limit is a page size, not a total-result cap.
            next_offset = offset + len(items)
            actual_cursor = cursor_at(len(items)) if next_offset < len(ordered) else None
            return envelope({"items": items, "generation": generation, "coverage": cov,
                             "content": base["content"], "match": base["match"], "unavailable_body_matches": metadata_matches,
                             "noncontent_body_matches": noncontent_matches,
                             "effective_queries": expanded, "matched_count": full_count,
                             "partial_term_matches": len(ordered) - full_count,
                             "next_cursor": actual_cursor}, warnings=warnings, partial=candidate_limit_reached)
        return bounded_page(units, 0, response, budget, cursor_at)


def validate_read(request):
    fields(request, {"items", "mode", "context", "max_chars", "cursor"})
    if request.get("cursor"):
        cursor = decode_cursor(request["cursor"], "read")
        if cursor.get("read_version") != READ_VERSION:
            raise XhsError("CURSOR_STALE", "Read format changed; repeat the original read")
        if "items" in request or "mode" in request or "context" in request:
            raise XhsError("INVALID_ARGUMENT", "Use cursor with max_chars only", exit_code=2)
        base = validate_read(cursor.get("request", {}))[0]
        integer(cursor.get("offset"), "cursor offset", 0, 1000000)
        return base, cursor, request.get("max_chars", 12000)
    items = request.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise XhsError("INVALID_ARGUMENT", "Provide 1-10 items", exit_code=2)
    seen = set()
    for item in items:
        fields(item, {"note_id", "revision_id", "block_ids", "asset_ids"}, {"note_id", "revision_id"})
        safe_id(item["note_id"], "note_id")
        safe_id(item["revision_id"], "revision_id")
        key = (item["note_id"], item["revision_id"])
        if key in seen:
            raise XhsError("INVALID_ARGUMENT", "Request each note revision only once", exit_code=2)
        seen.add(key)
        for key in ["block_ids", "asset_ids"]:
            if key in item:
                if not isinstance(item[key], list) or len(item[key]) > 100:
                    raise XhsError("INVALID_ARGUMENT", f"Invalid {key}", exit_code=2)
                for entry in item[key]:
                    safe_id(entry, key)
    mode = request.get("mode", "evidence")
    if mode not in {"evidence", "full", "asset"}:
        raise XhsError("INVALID_ARGUMENT", "mode must be evidence, full or asset", exit_code=2)
    if 'context' in request and mode != 'evidence':
        raise XhsError('INVALID_ARGUMENT', 'context is only supported in evidence mode', exit_code=2)
    base = {"items": items, "mode": mode}
    if mode == 'evidence':
        base['context'] = integer(request.get('context', 1), 'context', 0, 4)
    return base, None, request.get("max_chars", 12000)


def read(store, request):
    base, cursor, budget = validate_read(request)
    units, errors = [], []
    with store.read() as db:
        for item in base["items"]:
            row = db.execute("SELECT r.payload,n.deleted FROM revisions r JOIN notes n ON n.id=r.note_id "
                             "WHERE r.id=? AND r.note_id=?", (item["revision_id"], item["note_id"])).fetchone()
            if not row or row["deleted"]:
                errors.append({"note_id": item["note_id"], "code": "REVISION_NOT_FOUND"})
                continue
            note = json.loads(row["payload"])
            common = {"note_id": item["note_id"], "revision_id": item["revision_id"],
                      "title": note.get("title", ""), "source_kind": note.get("source_kind", "xiaohongshu"),
                      "source_url": note.get("canonical_url", note.get("source_url", "").split("?")[0]),
                      "content_state": note.get("content_state", "unknown"), "gaps": note.get("gaps", [])}
            common["content_scope"] = note.get("content_scope", "archive")
            if base["mode"] == "asset":
                wanted = set(item.get("asset_ids", []))
                assets = note.get("images", [])
                common['media_state'] = note.get('media_state', 'unknown')
                common['expected_images'] = note.get('expected_images')
                if not assets:
                    units.append(dict(common, asset=None))
                    if note.get('media_state') == 'deferred':
                        code = 'MEDIA_DEFERRED'
                    elif note.get('expected_images') == 0:
                        code = 'ASSET_NOT_FOUND' if wanted else None
                    else:
                        code = 'IMAGE_LIST_INCOMPLETE' if note.get('expected_images') else 'IMAGE_COUNT_UNKNOWN'
                    if code:
                        errors.append({'note_id': item['note_id'], 'code': code})
                    continue
                if note.get("media_state") == "deferred" and not any(a.get("object_path") for a in assets):
                    errors.append({"note_id": item["note_id"], "code": "MEDIA_DEFERRED"})
                    units.append(dict(common, asset=None))
                    continue
                missing = wanted - {a["asset_id"] for a in assets}
                if not wanted:
                    if note.get('expected_images') is None:
                        errors.append({'note_id': item['note_id'], 'code': 'IMAGE_COUNT_UNKNOWN'})
                    elif note.get('expected_images') != len(assets):
                        errors.append({'note_id': item['note_id'], 'code': 'IMAGE_LIST_INCOMPLETE'})
                    elif note.get('image_manifest_complete') is False:
                        errors.append({'note_id': item['note_id'], 'code': 'IMAGE_LIST_UNVERIFIED'})
                if wanted and missing == wanted:
                    units.append(dict(common, asset=None))
                errors.extend({"note_id": item["note_id"], "asset_id": a, "code": "ASSET_NOT_FOUND"} for a in sorted(missing))
                for asset in assets:
                    if wanted and asset["asset_id"] not in wanted:
                        continue
                    result = {k: v for k, v in asset.items() if k not in {"ocr_lines", "source_url"}}
                    path = store.object_path(asset["object_path"]) if asset.get("object_path") else None
                    if path and path.is_file():
                        result["path"] = str(path)
                    else:
                        errors.append({"note_id": item["note_id"], "asset_id": asset["asset_id"], "code": "ASSET_MISSING"})
                    units.append(dict(common, asset=result))
                continue
            blocks = [dict(b) for b in db.execute(
                "SELECT block_id,ordinal,kind,text,location FROM evidence_blocks WHERE revision_id=? ORDER BY ordinal",
                (item["revision_id"],))]
            wanted = set(item.get("block_ids", []))
            missing = wanted - {b["block_id"] for b in blocks}
            errors.extend({"note_id": item["note_id"], "block_id": b, "code": "BLOCK_NOT_FOUND"} for b in sorted(missing))
            indices = set(range(len(blocks))) if base["mode"] == "full" else set()
            if base["mode"] == "evidence":
                anchors = [i for i, b in enumerate(blocks) if b["block_id"] in wanted] if wanted else list(range(min(3, len(blocks))))
                for i in anchors:
                    radius = base['context']
                    indices.update(range(max(0, i - radius), min(len(blocks), i + radius + 1)))
            for i in sorted(indices):
                block = blocks[i]
                block["location"] = json.loads(block["location"])
                units.append(dict(common, block=block))
            if not blocks:
                units.append(dict(common, block=None))
    offset = cursor["offset"] if cursor else 0
    integer(offset, "cursor offset", 0, len(units))
    def cursor_at(index):
        return encode_cursor({"v": 1, "kind": "read", "read_version": READ_VERSION, "request": base, "offset": index})
    def response(items, next_cursor):
        def key(index):
            return (units[index]["note_id"], units[index]["revision_id"])
        continued = key(offset - 1) if offset else None
        following = key(offset + len(items)) if offset + len(items) < len(units) else None
        grouped = group_evidence(items, continued_key=continued, next_key=following)
        return envelope({"items": grouped, "mode": base["mode"], "errors": errors, "next_cursor": next_cursor}, partial=bool(errors))
    return bounded_page(units, offset, response, budget, cursor_at)

