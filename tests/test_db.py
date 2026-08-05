import pytest

from bqpp.db import Database
from bqpp.models import Question, SourceDocument, UsageEntry


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


def _doc(i="d1"):
    return SourceDocument(
        id=i, source_id="hf-oab-exams", url="u", fetched_at="2026-08-05T00:00:00Z",
        kind="dataset", banca="FGV", carreira="oab", certame="OAB 2010-01", exam_year=2010,
    )


def _q(i="q1", doc="d1", fmt="mcq4"):
    return Question(
        id=i, source_doc_id=doc, question_number="1", format=fmt, stem="Enunciado",
        choices=[{"label": "A", "text": "a"}], answer_key="A",
    )


def test_upsert_is_idempotent(db):
    db.upsert_source_document(_doc())
    assert db.upsert_question(_q()) is True
    assert db.upsert_question(_q()) is False  # already present, skipped
    assert db.upsert_question(_q(), force=True) is True
    assert db.stats()["total"] == 1


def test_roundtrip_preserves_json_columns(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q())
    db.update_classification(
        "q1", discipline="direito-processual-penal", subtopic_ids=["T1.2", "T2.4"],
        difficulty="medium", classified_note=None, classify_model="m",
        classified_at="2026-08-05T00:00:00Z",
    )
    got = db.get_question("q1")
    assert got.subtopic_ids == ["T1.2", "T2.4"]
    assert got.discipline == "direito-processual-penal"


def test_iter_filters(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q("a"))
    db.upsert_question(_q("b"))
    db.update_classification(
        "a", discipline="other", subtopic_ids=[], difficulty="easy",
        classified_note="n", classify_model="m", classified_at="t",
    )
    assert [q.id for q in db.iter_questions(unclassified=True)] == ["b"]


def test_iter_by_subtopic_matches_secondary_ids(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q("a"))
    db.update_classification(
        "a", discipline="direito-processual-penal", subtopic_ids=["T2.4", "T1.2"],
        difficulty="easy", classified_note=None, classify_model="m", classified_at="t",
    )
    assert [q.id for q in db.iter_questions(subtopic="T1.2")] == ["a"]
    assert list(db.iter_questions(subtopic="T3.1")) == []


def test_usage_log_blocks_reuse(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q())
    db.record_usage(
        UsageEntry(question_id="q1", semester="2026.1", subtopic_id="T1.2", used_at="t")
    )
    assert db.used_question_ids() == {"q1"}


def test_stats_on_empty_db(tmp_path):
    d = Database.connect(tmp_path / "e.sqlite")
    d.init_schema()
    s = d.stats()
    assert s["total"] == 0 and s["by_vet_status"] == {} and s["by_subtopic"] == {}
    d.close()
