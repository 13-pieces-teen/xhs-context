"""Synthetic FTS5 mechanism probe; no XHS data, models or extra packages.

Run: python research/fts5_probe.py
The manually segmented control is NOT a production tokenizer evaluation.
"""

import json
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    original = "马德里小吃推荐，图二介绍土豆饼。"
    segmented = "马德里 小吃 推荐 图二 介绍 土豆饼"
    queries = ["马德里", "小吃", "马德里 小吃", "土豆饼"]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE raw_fts USING fts5(text, tokenize='unicode61')")
    db.execute("CREATE VIRTUAL TABLE segmented_fts USING fts5(text, tokenize='unicode61')")
    db.execute("CREATE VIRTUAL TABLE trigram_fts USING fts5(text, tokenize='trigram')")
    db.execute("INSERT INTO raw_fts VALUES (?)", (original,))
    db.execute("INSERT INTO segmented_fts VALUES (?)", (segmented,))
    db.execute("INSERT INTO trigram_fts VALUES (?)", (original,))
    rows = []
    for query in queries:
        row = {"query": query}
        for table in ("raw_fts", "segmented_fts", "trigram_fts"):
            # Table identifiers come only from the constant allowlist above.
            row[table] = db.execute(
                f"SELECT count(*) FROM {table} WHERE {table} MATCH ?", (query,)
            ).fetchone()[0]
        row["literal_substring"] = query in original
        rows.append(row)
    db.close()
    result = {
        "experiment": "synthetic_fts5_chinese_matching",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "source_text": original,
        "manual_segmented_control": segmented,
        "results": rows,
        "limits": [
            "One synthetic document; no real favorites were read.",
            "Manual segmentation demonstrates the mechanism, not tokenizer quality.",
            "No accuracy, latency, OCR or end-to-end product acceptance was measured.",
        ],
    }
    target = Path(__file__).with_name("fts5-probe.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
