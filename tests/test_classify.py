import json

import pytest

from bqpp.classify import CLASSIFY_SCHEMA, run_classify
from bqpp.config import load_taxonomy
from bqpp.db import Database
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import Question, SourceDocument


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    d.upsert_source_document(
        SourceDocument(id="d", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    d.upsert_question(
        Question(
            id="q1", source_doc_id="d", question_number="1", format="mcq4",
            stem="Sobre prisão preventiva...", choices=[{"label": "A", "text": "a"}],
            answer_key="A",
        )
    )
    yield d
    d.close()


def _payload(**over):
    base = {
        "discipline": "direito-processual-penal",
        "subtopic_ids": ["T1.2"],
        "difficulty": "medium",
        "note": "",
    }
    return json.dumps({**base, **over}, ensure_ascii=False)


def test_schema_is_openai_strict_compatible():
    assert CLASSIFY_SCHEMA["additionalProperties"] is False
    assert set(CLASSIFY_SCHEMA["required"]) == set(CLASSIFY_SCHEMA["properties"])


def test_happy_path_writes_classification(db):
    client = LLMClient(FakeBackend([_payload()]))
    assert run_classify(db, client, load_taxonomy()) == 1
    q = db.get_question("q1")
    assert q.subtopic_ids == ["T1.2"] and q.discipline == "direito-processual-penal"
    assert q.classified_at and q.classify_model


def test_prompt_carries_taxonomy_and_verbatim_question(db):
    be = FakeBackend([_payload()])
    run_classify(db, LLMClient(be), load_taxonomy())
    user = be.calls[0]["user"]
    assert "T1.2" in user and "Prisão temporária" in user
    assert "Sobre prisão preventiva" in user


def test_classification_runs_on_the_fast_tier(db):
    be = FakeBackend([_payload()])
    run_classify(db, LLMClient(be), load_taxonomy())
    assert be.calls[0]["tier"] == "fast"


def test_invalid_subtopic_id_retries_once_then_stores_unclassified(db):
    # spec §9.3: an invalid subtopic id ⇒ retry once with the error, then unclassified
    be = FakeBackend([_payload(subtopic_ids=["T9.9"]), _payload(subtopic_ids=["T9.9"])])
    run_classify(db, LLMClient(be), load_taxonomy())
    q = db.get_question("q1")
    assert q.subtopic_ids == []
    assert "T9.9" in (q.classified_note or "")
    assert len(be.calls) == 2


def test_valid_retry_is_accepted(db):
    be = FakeBackend([_payload(subtopic_ids=["T9.9"]), _payload(subtopic_ids=["T2.4"])])
    run_classify(db, LLMClient(be), load_taxonomy())
    assert db.get_question("q1").subtopic_ids == ["T2.4"]


def test_only_unclassified_skips_done_work(db):
    run_classify(db, LLMClient(FakeBackend([_payload()])), load_taxonomy())
    be = FakeBackend([])
    assert run_classify(db, LLMClient(be), load_taxonomy()) == 0
    assert be.calls == []


def test_dry_run_writes_nothing(db):
    be = FakeBackend([_payload()])
    run_classify(db, LLMClient(be), load_taxonomy(), dry_run=True)
    assert db.get_question("q1").classified_at is None


def test_one_failing_question_does_not_abort_the_batch(db):
    db.upsert_question(
        Question(id="q2", source_doc_id="d", question_number="2", format="mcq4",
                 stem="Outra questão", choices=[{"label": "A", "text": "a"}], answer_key="A")
    )
    # q1 exhausts its 3 attempts on garbage; q2 then succeeds
    be = FakeBackend(["bad", "bad", "bad", _payload()])
    assert run_classify(db, LLMClient(be), load_taxonomy()) == 1
    assert db.get_question("q1").classified_at is None
    assert db.get_question("q2").classified_at is not None
