"""Schema migration against a database created by an earlier milestone.

The live corpus.sqlite holds the usage log — the only record of what was actually
taught, and not reconstructible from any source. These tests exist so a migration
can never be shipped without proving it preserves that.
"""

import sqlite3

import pytest

from bqpp.db import Database
from bqpp.models import Question, SourceDocument, UsageEntry

# The questions table exactly as M2 shipped it: no stem_context.
M2_SCHEMA = """
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
"""


@pytest.fixture
def m2_database(tmp_path):
    """A database as it existed before M3, carrying a question and a usage-log row."""
    path = tmp_path / "m2.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(M2_SCHEMA)
    conn.execute(
        "INSERT INTO source_documents VALUES "
        "('d1','oab-2f-penal','https://s.oab.org.br/x.pdf','t','gabarito_justificado',"
        "'FGV','oab','OAB 44º Exame (2ª fase)',2025,NULL)"
    )
    conn.execute(
        "INSERT INTO questions (id, source_doc_id, question_number, format, stem, "
        "answer_rationale, discipline, subtopic_ids, classify_model, classified_at, "
        "vet_status, pedagogy_note) VALUES "
        "('q1','d1','1','dissertativa','enunciado antigo','fundamentação da banca',"
        "'direito-processual-penal','[\"T2.4\"]','gemini','2026-08-08','flagged','nota')"
    )
    conn.execute(
        "INSERT INTO usage_log VALUES ('q1','2026.2','T2.4','2026-08-08','abriu a aula')"
    )
    conn.commit()
    conn.close()
    return path


def test_migration_adds_the_column(m2_database):
    db = Database.connect(m2_database)
    assert "stem_context" not in {r[1] for r in db.conn.execute("PRAGMA table_info(questions)")}
    db.init_schema()
    assert "stem_context" in {r[1] for r in db.conn.execute("PRAGMA table_info(questions)")}
    db.close()


def test_migration_preserves_every_existing_question(m2_database):
    db = Database.connect(m2_database)
    db.init_schema()
    q = db.get_question("q1")
    assert q is not None
    assert q.stem == "enunciado antigo"
    assert q.answer_rationale == "fundamentação da banca"
    assert q.subtopic_ids == ["T2.4"]
    assert q.vet_status == "flagged"
    assert q.pedagogy_note == "nota"
    assert q.stem_context is None, "existing rows default to NULL"
    db.close()


def test_migration_preserves_the_usage_log(m2_database):
    """The one table that cannot be rebuilt from any source."""
    db = Database.connect(m2_database)
    db.init_schema()
    entries = db.usage_entries()
    assert len(entries) == 1
    assert entries[0].question_id == "q1"
    assert entries[0].note == "abriu a aula"
    db.close()


def test_migration_is_idempotent(m2_database):
    """init_schema runs on every CLI invocation; a second pass must not error."""
    db = Database.connect(m2_database)
    db.init_schema()
    db.init_schema()
    db.init_schema()
    cols = [r[1] for r in db.conn.execute("PRAGMA table_info(questions)")]
    assert cols.count("stem_context") == 1
    db.close()


def test_a_fresh_database_has_the_column_without_migrating(tmp_path):
    db = Database.connect(tmp_path / "fresh.sqlite")
    db.init_schema()
    assert "stem_context" in {r[1] for r in db.conn.execute("PRAGMA table_info(questions)")}
    db.close()


def test_stem_context_round_trips(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    comando = "Acerca da prova no processo penal, julgue os itens a seguir."
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="certo_errado", stem="item",
                 stem_context=comando)
    )
    assert db.get_question("q1").stem_context == comando
    db.close()


def test_stem_context_survives_a_force_reingest(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="certo_errado", stem="a", stem_context="ctx")
    )
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="certo_errado", stem="b", stem_context="ctx2"),
        force=True,
    )
    assert db.get_question("q1").stem_context == "ctx2"
    db.close()


def test_usage_log_survives_a_force_reingest_of_its_question(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    db.upsert_question(Question(id="q1", source_doc_id="d1", format="mcq4", stem="a"))
    db.record_usage(UsageEntry(question_id="q1", semester="2026.2", subtopic_id="T1.2",
                               used_at="t", note="foi bem"))
    db.upsert_question(Question(id="q1", source_doc_id="d1", format="mcq4", stem="b"), force=True)
    entries = db.usage_entries()
    assert len(entries) == 1 and entries[0].note == "foi bem"
    db.close()


def test_context_in_its_own_column_keeps_sibling_items_distinct(tmp_path):
    """Amendment C10, the reason this column exists.

    A real comando runs ~480 chars and stem_hash keys on the first 300, so
    prefixing it to each item makes every item in a block hash identically and
    M2's dedup drops all but the first.
    """
    from bqpp.models import stem_hash

    comando = (
        "Determinado juiz de direito, ao proferir sentença em processo criminal, fundamentou "
        "sua decisão condenatória exclusivamente em elementos informativos colhidos durante o "
        "inquérito policial, sem que tais elementos tivessem sido submetidos ao contraditório "
        "judicial. A defesa sustentou, em sede de apelação, a violação ao sistema do livre "
        "convencimento motivado e ao standard probatório exigido. Com base nessa situação "
        "hipotética, julgue os itens que se seguem."
    )
    items = [
        "A condenação pode fundar-se exclusivamente em elementos do inquérito policial.",
        "O livre convencimento motivado dispensa a fundamentação expressa da decisão.",
        "O standard probatório penal exige prova além da dúvida razoável.",
    ]
    assert len(comando) > 300, "the comando must exceed the hash window for this to bite"
    prefixed = {stem_hash(comando + "\n" + i) for i in items}
    separate = {stem_hash(i) for i in items}
    assert len(prefixed) == 1, "prefixing collapses the block to one hash — the bug"
    assert len(separate) == 3, "a separate column keeps the items distinct"
