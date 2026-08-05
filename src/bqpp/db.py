"""The only module in the project that emits SQL."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bqpp.models import Question, SourceDocument, UsageEntry, VetReason

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,
  fetched_at TEXT NOT NULL, kind TEXT NOT NULL, banca TEXT, carreira TEXT,
  certame TEXT, exam_year INTEGER, local_path TEXT
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  source_doc_id TEXT NOT NULL REFERENCES source_documents(id),
  question_number TEXT, format TEXT NOT NULL, stem TEXT NOT NULL,
  choices TEXT, answer_key TEXT, answer_rationale TEXT,
  nullified INTEGER DEFAULT 0,
  discipline TEXT, subtopic_ids TEXT, difficulty TEXT, classified_note TEXT,
  classify_model TEXT, classified_at TEXT,
  vet_status TEXT DEFAULT 'unvetted', vet_reasons TEXT, pedagogy_note TEXT,
  vet_model TEXT, vetted_at TEXT
);
CREATE TABLE IF NOT EXISTS usage_log (
  question_id TEXT REFERENCES questions(id), semester TEXT NOT NULL,
  subtopic_id TEXT NOT NULL, used_at TEXT, note TEXT,
  PRIMARY KEY (question_id, semester)
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, called_at TEXT NOT NULL, stage TEXT,
  backend TEXT, model TEXT, tier TEXT, attempt INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, ok INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_vet ON questions(vet_status);
CREATE INDEX IF NOT EXISTS idx_q_discipline ON questions(discipline);
"""


class Database:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def connect(cls, path: Path) -> Database:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn)

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- writes -------------------------------------------------------
    def _exists(self, table: str, row_id: str) -> bool:
        cur = self.conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,))
        return cur.fetchone() is not None

    def upsert_source_document(self, doc: SourceDocument, force: bool = False) -> bool:
        if self._exists("source_documents", doc.id) and not force:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO source_documents "
            "(id, source_id, url, fetched_at, kind, banca, carreira, certame, exam_year, local_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc.id, doc.source_id, doc.url, doc.fetched_at, doc.kind, doc.banca,
             doc.carreira, doc.certame, doc.exam_year, doc.local_path),
        )
        self.conn.commit()
        return True

    def upsert_question(self, q: Question, force: bool = False) -> bool:
        if self._exists("questions", q.id) and not force:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO questions "
            "(id, source_doc_id, question_number, format, stem, choices, answer_key, "
            " answer_rationale, nullified, discipline, subtopic_ids, difficulty, classified_note, "
            " classify_model, classified_at, vet_status, vet_reasons, pedagogy_note, vet_model, "
            " vetted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (q.id, q.source_doc_id, q.question_number, q.format, q.stem,
             json.dumps(q.choices, ensure_ascii=False) if q.choices else None,
             q.answer_key, q.answer_rationale, int(q.nullified), q.discipline,
             json.dumps(q.subtopic_ids), q.difficulty, q.classified_note,
             q.classify_model, q.classified_at, q.vet_status,
             json.dumps([r.model_dump() for r in q.vet_reasons], ensure_ascii=False),
             q.pedagogy_note, q.vet_model, q.vetted_at),
        )
        self.conn.commit()
        return True

    def update_classification(self, qid: str, **f: Any) -> None:
        self.conn.execute(
            "UPDATE questions SET discipline=?, subtopic_ids=?, difficulty=?, classified_note=?, "
            "classify_model=?, classified_at=? WHERE id=?",
            (f["discipline"], json.dumps(f["subtopic_ids"]), f["difficulty"],
             f["classified_note"], f["classify_model"], f["classified_at"], qid),
        )
        self.conn.commit()

    def update_vetting(self, qid: str, **f: Any) -> None:
        self.conn.execute(
            "UPDATE questions SET vet_status=?, vet_reasons=?, pedagogy_note=?, vet_model=?, "
            "vetted_at=? WHERE id=?",
            (f["vet_status"],
             json.dumps([r.model_dump() for r in f["vet_reasons"]], ensure_ascii=False),
             f.get("pedagogy_note"), f.get("vet_model"), f.get("vetted_at"), qid),
        )
        self.conn.commit()

    def record_usage(self, entry: UsageEntry) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO usage_log (question_id, semester, subtopic_id, used_at, note) "
            "VALUES (?,?,?,?,?)",
            (entry.question_id, entry.semester, entry.subtopic_id, entry.used_at, entry.note),
        )
        self.conn.commit()

    def log_llm_call(self, **f: Any) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls (called_at, stage, backend, model, tier, attempt, "
            "input_tokens, output_tokens, latency_ms, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f["called_at"], f.get("stage"), f.get("backend"), f.get("model"), f.get("tier"),
             f.get("attempt"), f.get("input_tokens"), f.get("output_tokens"),
             f.get("latency_ms"), int(f.get("ok", True)), f.get("error")),
        )
        self.conn.commit()

    # ---- reads --------------------------------------------------------
    @staticmethod
    def _to_question(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"], source_doc_id=row["source_doc_id"],
            question_number=row["question_number"], format=row["format"], stem=row["stem"],
            choices=json.loads(row["choices"]) if row["choices"] else None,
            answer_key=row["answer_key"], answer_rationale=row["answer_rationale"],
            nullified=bool(row["nullified"]), discipline=row["discipline"],
            subtopic_ids=json.loads(row["subtopic_ids"]) if row["subtopic_ids"] else [],
            difficulty=row["difficulty"], classified_note=row["classified_note"],
            classify_model=row["classify_model"], classified_at=row["classified_at"],
            vet_status=row["vet_status"],
            vet_reasons=[VetReason(**r) for r in json.loads(row["vet_reasons"] or "[]")],
            pedagogy_note=row["pedagogy_note"], vet_model=row["vet_model"],
            vetted_at=row["vetted_at"],
        )

    def get_question(self, qid: str) -> Question | None:
        row = self.conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        return self._to_question(row) if row else None

    def get_source_document(self, doc_id: str) -> SourceDocument | None:
        row = self.conn.execute(
            "SELECT * FROM source_documents WHERE id=?", (doc_id,)
        ).fetchone()
        return SourceDocument(**dict(row)) if row else None

    def iter_questions(self, *, unclassified: bool = False, unvetted: bool = False,
                       subtopic: str | None = None) -> Iterator[Question]:
        sql, params = "SELECT * FROM questions WHERE 1=1", []
        if unclassified:
            sql += " AND classified_at IS NULL"
        if unvetted:
            sql += " AND vet_status='unvetted'"
        if subtopic:
            # subtopic_ids is a JSON array; the quoted form makes "T1.2" not match "T1.21"
            sql += " AND subtopic_ids LIKE ?"
            params.append(f'%"{subtopic}"%')
        sql += " ORDER BY id"
        for row in self.conn.execute(sql, params):
            yield self._to_question(row)

    def used_question_ids(self, before_semester: str | None = None) -> set[str]:
        sql, params = "SELECT question_id FROM usage_log", []
        if before_semester:
            sql += " WHERE semester < ?"
            params.append(before_semester)
        return {r[0] for r in self.conn.execute(sql, params)}

    def stats(self) -> dict[str, Any]:
        def counts(sql: str) -> dict[str, int]:
            return {str(r[0]): r[1] for r in self.conn.execute(sql) if r[0] is not None}

        total = self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        by_subtopic: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT subtopic_ids FROM questions WHERE subtopic_ids IS NOT NULL"
        ):
            for sid in json.loads(row[0] or "[]"):
                by_subtopic[sid] = by_subtopic.get(sid, 0) + 1
        return {
            "total": total,
            "by_format": counts("SELECT format, COUNT(*) FROM questions GROUP BY format"),
            "by_vet_status": counts(
                "SELECT vet_status, COUNT(*) FROM questions "
                "WHERE classified_at IS NOT NULL GROUP BY vet_status"
            ),
            "by_discipline": counts(
                "SELECT discipline, COUNT(*) FROM questions GROUP BY discipline"
            ),
            "by_subtopic": by_subtopic,
            "sources": counts(
                "SELECT source_id, COUNT(*) FROM source_documents GROUP BY source_id"
            ),
        }
