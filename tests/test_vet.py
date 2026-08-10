import json

import pytest

from bqpp.db import Database
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import Question, SourceDocument, VetReason
from bqpp.vet import VET_SCHEMA, _merge_verdict, apply_rules, load_watchlist, run_vet


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    d.upsert_source_document(
        SourceDocument(id="d", source_id="s", url="u", fetched_at="t",
                       kind="dataset", exam_year=2015)
    )
    yield d
    d.close()


def _q(qid="q1", **over):
    base = {
        "id": qid, "source_doc_id": "d", "question_number": "1", "format": "mcq4",
        "stem": "s", "choices": [{"label": "A", "text": "a"}], "answer_key": "A",
        "discipline": "direito-processual-penal", "subtopic_ids": ["T1.2"],
    }
    return Question(**{**base, **over})


def _verdict(**over):
    base = {"verdict": "ok", "reasons": [], "pedagogy_note": "boa questão"}
    return json.dumps({**base, **over}, ensure_ascii=False)


def test_schema_is_openai_strict_compatible():
    assert VET_SCHEMA["additionalProperties"] is False
    assert set(VET_SCHEMA["required"]) == set(VET_SCHEMA["properties"])
    item = VET_SCHEMA["properties"]["reasons"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_nullified_is_rejected_without_an_llm_call(db):
    db.upsert_question(_q(nullified=True))
    be = FakeBackend([])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "rejected"
    assert [r.code for r in q.vet_reasons] == ["anulada"]
    assert be.calls == []


def test_missing_gabarito_is_flagged_and_still_vetted_by_llm(db):
    db.upsert_question(_q(answer_key=None))
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "flagged"
    assert "no_gabarito" in [r.code for r in q.vet_reasons]
    assert len(be.calls) == 1


def test_vetting_runs_on_the_strong_tier(db):
    db.upsert_question(_q())
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    assert be.calls[0]["tier"] == "strong"


def test_watchlist_entry_is_injected_for_affected_old_questions(db):
    db.upsert_question(_q())  # exam_year 2015, subtopic T1.2 -> pacote-anticrime
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    assert "13.964" in be.calls[0]["user"] or "Anticrime" in be.calls[0]["user"]


def test_unaffected_subtopic_gets_no_watchlist_injection(db):
    watchlist = load_watchlist()
    # Asserting on the block header rather than on one entry's text: otherwise the
    # test keeps passing once a new entry covers the subtopic it picked.
    assert "T3.3" not in {s for e in watchlist for s in e.affects}, (
        "T3.3 is now on the watchlist — pick another uncovered subtopic here"
    )
    db.upsert_question(_q(subtopic_ids=["T3.3"]))
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), watchlist)
    assert "Watchlist" not in be.calls[0]["user"]


def test_question_newer_than_the_change_gets_no_injection(tmp_path):
    watchlist = load_watchlist()
    # Derived, not hardcoded: an exam as new as the newest entry predates nothing,
    # and the test keeps its meaning when the professor adds a more recent law.
    newest = max(e.effective.year for e in watchlist)
    d = Database.connect(tmp_path / "n.sqlite")
    d.init_schema()
    d.upsert_source_document(
        SourceDocument(id="d", source_id="s", url="u", fetched_at="t",
                       kind="dataset", exam_year=newest)
    )
    d.upsert_question(_q())
    be = FakeBackend([_verdict()])
    run_vet(d, LLMClient(be), watchlist)
    assert "Watchlist" not in be.calls[0]["user"]
    d.close()


def test_resposta_mudou_mas_util_maps_to_flagged_not_rejected(db):
    db.upsert_question(_q())
    be = FakeBackend(
        [
            _verdict(
                verdict="rejected",
                reasons=[{"code": "resposta_mudou_mas_util", "detail": "art. 316 CPP mudou"}],
            )
        ]
    )
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "flagged"
    assert q.pedagogy_note


def test_genuinely_rejected_stays_rejected(db):
    db.upsert_question(_q())
    be = FakeBackend(
        [_verdict(verdict="rejected", reasons=[{"code": "ambigua", "detail": "duas corretas"}])]
    )
    run_vet(db, LLMClient(be), load_watchlist())
    assert db.get_question("q1").vet_status == "rejected"


def test_only_unvetted_skips_done_work(db):
    db.upsert_question(_q())
    run_vet(db, LLMClient(FakeBackend([_verdict()])), load_watchlist())
    be = FakeBackend([])
    assert run_vet(db, LLMClient(be), load_watchlist()) == 0


def test_apply_rules_is_pure():
    status, reasons, entries = apply_rules(_q(nullified=True), load_watchlist())
    assert status == "rejected" and reasons[0].code == "anulada" and entries == []


def test_apply_rules_collects_gabarito_preliminar_reason():
    status, reasons, _entries = apply_rules(_q(answer_key_provisional=True), load_watchlist())
    assert status is None
    assert any(r.code == "gabarito_preliminar" for r in reasons)


def test_provisional_answer_key_is_flagged_and_still_vetted_by_llm(db):
    db.upsert_question(_q(answer_key_provisional=True))
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "flagged"
    assert "gabarito_preliminar" in [r.code for r in q.vet_reasons]
    assert len(be.calls) == 1


def test_nullified_with_provisional_key_stays_terminally_rejected(db):
    """A nullified question short-circuits apply_rules entirely — it must never
    come back as merely flagged just because its key was also provisional."""
    db.upsert_question(_q(nullified=True, answer_key_provisional=True))
    be = FakeBackend([])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "rejected"
    assert [r.code for r in q.vet_reasons] == ["anulada"]
    assert be.calls == []


def test_merge_verdict_escalates_on_either_code():
    for code in ("no_gabarito", "gabarito_preliminar"):
        verdict, merged = _merge_verdict(
            [VetReason(code=code, detail="x")], {"verdict": "ok", "reasons": []}
        )
        assert verdict == "flagged"
        assert any(r.code == code for r in merged)


def test_merge_verdict_leaves_rejected_alone_without_softening_code():
    verdict, _ = _merge_verdict(
        [VetReason(code="gabarito_preliminar", detail="x")],
        {"verdict": "rejected", "reasons": [{"code": "ambigua", "detail": "y"}]},
    )
    assert verdict == "rejected"
