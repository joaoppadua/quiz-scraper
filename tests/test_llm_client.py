import pytest

from bqpp.db import Database
from bqpp.llm.base import LLMError
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend

SCHEMA = {
    "type": "object",
    "properties": {
        "discipline": {"type": "string"},
        "subtopic_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["discipline", "subtopic_ids"],
    "additionalProperties": False,
}

OK = '{"discipline": "direito-processual-penal", "subtopic_ids": ["T1.2"]}'


def _call(client, **kw):
    return client.complete_json(
        system="s", user="u", json_schema=SCHEMA, model_tier="fast", **kw
    )


def test_valid_first_try():
    be = FakeBackend([OK])
    assert _call(LLMClient(be))["subtopic_ids"] == ["T1.2"]
    assert len(be.calls) == 1


def test_retries_on_invalid_json_and_reprompts_with_the_error():
    be = FakeBackend(["not json at all", OK])
    assert _call(LLMClient(be))["discipline"] == "direito-processual-penal"
    assert len(be.calls) == 2
    retry_prompt = be.calls[1]["user"]
    assert "invalid" in retry_prompt.lower()
    assert "JSONDecodeError" in retry_prompt


def test_retries_on_schema_violation():
    be = FakeBackend(['{"discipline": "x"}', '{"discipline": "other", "subtopic_ids": []}'])
    assert _call(LLMClient(be))["subtopic_ids"] == []
    assert len(be.calls) == 2


def test_fails_loudly_after_max_attempts_and_never_returns_unvalidated():
    be = FakeBackend(["bad", "still bad", "nope"])
    with pytest.raises(LLMError):
        _call(LLMClient(be, max_attempts=3))
    assert len(be.calls) == 3


def test_strips_markdown_code_fences():
    be = FakeBackend(['```json\n{"discipline": "other", "subtopic_ids": []}\n```'])
    assert _call(LLMClient(be))["discipline"] == "other"


def test_logs_every_call_to_db_including_tier(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    be = FakeBackend(["bad", '{"discipline": "other", "subtopic_ids": []}'])
    LLMClient(be, db=db).complete_json(
        system="s", user="u", json_schema=SCHEMA, model_tier="fast", stage="classify"
    )
    rows = list(
        db.conn.execute(
            "SELECT stage, attempt, ok, tier, model, input_tokens, output_tokens "
            "FROM llm_calls ORDER BY id"
        )
    )
    assert [r["attempt"] for r in rows] == [1, 2]
    assert [bool(r["ok"]) for r in rows] == [False, True]
    # spec §9.1: every call logs model, tier, token counts and latency
    assert all(r["tier"] == "fast" for r in rows)
    assert all(r["model"] == "fake-fast" for r in rows)
    assert all(r["input_tokens"] == 10 and r["output_tokens"] == 20 for r in rows)
    db.close()
