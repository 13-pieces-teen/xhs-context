"""Library-specific equivalent names. Stored in the database so backups and cursors follow them."""
from __future__ import annotations

import json

from .common import XhsError, dumps, text_field
from .text import ALIASES, spelled

META_KEY = "aliases"
MAX_GROUPS = 200
MAX_NAMES = 12


def custom_aliases(db):
    row = db.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def effective_aliases(db):
    merged = {canonical: tuple(names) for canonical, names in ALIASES.items()}
    for canonical, names in custom_aliases(db).items():
        # Defensive: a group key that is not one of its names would collapse the group.
        merged[canonical if canonical in names or canonical in ALIASES else names[0]] = tuple(names)
    return merged


def _clean(name):
    from .retrieval import terms_for
    text_field(name, "alias name", 50, False)
    value = spelled(name)
    if not terms_for(value):
        raise XhsError("INVALID_ARGUMENT", "An alias name must contain searchable text", exit_code=2)
    return value


def _describe(db):
    custom = custom_aliases(db)
    groups = []
    for canonical, names in sorted(effective_aliases(db).items()):
        builtin = ALIASES.get(canonical, ())
        source = "builtin" if canonical not in custom else "extended" if builtin else "custom"
        groups.append({"names": list(names), "source": source, "removable": [n for n in names if n not in builtin]})
    return {"groups": groups, "custom_groups": len(custom)}


def list_aliases(store):
    with store.read() as db:
        return _describe(db)


def _save(store, db, custom):
    if custom:
        db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (META_KEY, dumps(custom)))
    else:
        db.execute("DELETE FROM meta WHERE key=?", (META_KEY,))
    # Equivalents change candidates and ranking, so outstanding search cursors expire.
    store.bump(db)


def add_aliases(store, names):
    cleaned = list(dict.fromkeys(_clean(name) for name in names or []))
    if len(cleaned) < 2:
        raise XhsError("INVALID_ARGUMENT", "Provide at least two different names for one equivalent group", exit_code=2)
    with store.write() as db:
        custom, merged = custom_aliases(db), effective_aliases(db)
        owners = {name: canonical for canonical, group in merged.items() for name in group}
        touched = list(dict.fromkeys(owners[name] for name in cleaned if name in owners))
        builtin = [canonical for canonical in touched if canonical in ALIASES]
        if len(builtin) > 1:
            raise XhsError("CONFLICT", "These names belong to different built-in groups", exit_code=2,
                           details={"groups": [list(merged[c]) for c in builtin]})
        canonical = builtin[0] if builtin else touched[0] if touched else cleaned[0]
        group = list(dict.fromkeys([*merged.get(canonical, ()), *(n for c in touched for n in merged[c]), *cleaned]))
        if len(group) > MAX_NAMES:
            raise XhsError("INVALID_ARGUMENT", f"An equivalent group may contain at most {MAX_NAMES} names", exit_code=2)
        if tuple(group) == merged.get(canonical) and len(touched) <= 1:
            return dict(_describe(db), changed=False)
        for other in touched:
            if other != canonical:
                custom.pop(other, None)
        custom[canonical] = group
        if len(custom) > MAX_GROUPS:
            raise XhsError("INVALID_ARGUMENT", f"At most {MAX_GROUPS} custom groups are supported", exit_code=2)
        _save(store, db, custom)
        return dict(_describe(db), changed=True)


def remove_alias(store, name):
    value = _clean(name)
    with store.write() as db:
        custom = custom_aliases(db)
        canonical = next((c for c, group in custom.items() if value in group), None)
        if canonical is None:
            if any(value in group for group in ALIASES.values()):
                raise XhsError("CONFLICT", "Built-in equivalent names cannot be removed", exit_code=2)
            raise XhsError("NOT_FOUND", "No custom equivalent group contains this name", exit_code=2)
        if value in ALIASES.get(canonical, ()):
            raise XhsError("CONFLICT", "Built-in equivalent names cannot be removed", exit_code=2)
        group = [n for n in custom[canonical] if n != value]
        custom.pop(canonical)
        if len(group) >= 2 and tuple(group) != ALIASES.get(canonical):
            # The key must remain one of the group's names; query_groups rewrites queries to it.
            custom[canonical if canonical in group else group[0]] = group
        _save(store, db, custom)
        return dict(_describe(db), changed=True)
