"""JSONL export — the interchange format for anything downstream (spec §8)."""

from __future__ import annotations

import json
from pathlib import Path

from bqpp.db import Database


def export_jsonl(db: Database, out_path: Path) -> int:
    """One JSON object per line: the question joined with its source document."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for q in db.iter_questions():
            doc = db.get_source_document(q.source_doc_id)
            record = q.model_dump()
            record["source"] = doc.model_dump(mode="json") if doc else None
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n
