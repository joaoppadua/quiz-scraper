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


def test_force_reingest_preserves_classification_and_vetting(tmp_path):
    """`harvest --force` and `parse --force` re-write a question from its raw source.
    That must not silently discard the LLM work already done on it — the stages have
    their own --force for that."""
    from bqpp.models import Question, SourceDocument, VetReason

    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    db.upsert_question(Question(id="q1", source_doc_id="d1", format="mcq4", stem="original"))
    db.update_classification(
        "q1", discipline="direito-processual-penal", subtopic_ids=["T1.2"], difficulty="medium",
        classified_note=None, classify_model="gemini", classified_at="2026-08-08",
    )
    db.update_vetting(
        "q1", vet_status="flagged",
        vet_reasons=[VetReason(code="resposta_mudou_mas_util", detail="Pacote Anticrime")],
        pedagogy_note="ótima para discutir em sala", vet_model="gemini", vetted_at="2026-08-08",
    )

    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="mcq4", stem="re-parsed"), force=True
    )

    q = db.get_question("q1")
    assert q.stem == "re-parsed", "the raw content is refreshed"
    assert q.discipline == "direito-processual-penal"
    assert q.subtopic_ids == ["T1.2"]
    assert q.vet_status == "flagged"
    assert q.pedagogy_note == "ótima para discutir em sala"
    assert q.classify_model == "gemini" and q.vet_model == "gemini"
    db.close()


def test_a_caller_that_supplies_a_classification_still_wins(tmp_path):
    """`bqpp seed --force` sets the classification itself and must not be overridden."""
    from bqpp.models import Question, SourceDocument

    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="manual")
    )
    db.upsert_question(Question(id="q1", source_doc_id="d1", format="dissertativa", stem="a"))
    db.update_classification(
        "q1", discipline="other", subtopic_ids=["T1.1"], difficulty="easy",
        classified_note=None, classify_model="gemini", classified_at="2026-08-08",
    )
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="dissertativa", stem="b",
                 discipline="direito-processual-penal", subtopic_ids=["T3.3"],
                 classify_model="manual", classified_at="2026-08-09"),
        force=True,
    )
    q = db.get_question("q1")
    assert q.subtopic_ids == ["T3.3"] and q.classify_model == "manual"
    db.close()


def test_answer_key_provisional_survives_a_force_reingest_both_directions(tmp_path):
    """`answer_key_provisional` is not in `_STAGE_COLUMNS`, so `INSERT OR REPLACE` must
    overwrite it on `force=True` exactly as it does `stem_context` and `answer_key` —
    never carry the old value forward. A stale True after the key goes definitivo would
    keep an undeserved warning banner; a stale False after a re-harvest revealed the key
    is still preliminary would hide a real one. Pin both directions so a future edit to
    `_STAGE_COLUMNS` or `_carry_stage_fields` that starts carrying this field forward
    breaks a test instead of silently corrupting the corpus."""
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset")
    )

    # True -> False: a preliminary key that became definitivo on re-harvest.
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="mcq4", stem="a",
                 answer_key_provisional=True)
    )
    db.upsert_question(
        Question(id="q1", source_doc_id="d1", format="mcq4", stem="b",
                 answer_key_provisional=False),
        force=True,
    )
    assert db.get_question("q1").answer_key_provisional is False

    # False -> True: the inverse, a re-harvest that reveals the key is still preliminary.
    db.upsert_question(
        Question(id="q2", source_doc_id="d1", format="mcq4", stem="a",
                 answer_key_provisional=False)
    )
    db.upsert_question(
        Question(id="q2", source_doc_id="d1", format="mcq4", stem="b",
                 answer_key_provisional=True),
        force=True,
    )
    assert db.get_question("q2").answer_key_provisional is True
    db.close()
