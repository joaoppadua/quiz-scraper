"""Backend tests run against injected SDK stubs — no network, no API keys."""

import pytest

from bqpp.llm.base import LLMTransientError
from bqpp.llm.fake_client import FakeBackend
from bqpp.llm.fallback import FallbackBackend
from bqpp.llm.gemini_client import GeminiBackend, to_gemini_schema
from bqpp.llm.openai_client import OpenAIBackend

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "string"}},
    "required": ["a"],
    "additionalProperties": False,
    "$schema": "http://json-schema.org/draft-07/schema#",
}


def test_gemini_schema_strips_unsupported_keys():
    out = to_gemini_schema(SCHEMA)
    assert "additionalProperties" not in out and "$schema" not in out
    assert out["required"] == ["a"] and out["properties"]["a"]["type"] == "string"


def test_gemini_schema_strips_nested_unsupported_keys():
    nested = {
        "type": "object",
        "properties": {
            "reasons": {
                "type": "array",
                "items": {"type": "object", "properties": {"code": {"type": "string"}},
                          "additionalProperties": False},
            }
        },
        "additionalProperties": False,
    }
    out = to_gemini_schema(nested)
    assert "additionalProperties" not in out["properties"]["reasons"]["items"]


class _StubGemini:
    def __init__(self):
        self.models = self
        self.seen = {}

    def generate_content(self, **kw):
        self.seen = kw

        class U:
            prompt_token_count, candidates_token_count = 11, 22

        class R:
            text, usage_metadata = '{"a": "ok"}', U()

        return R()


def test_gemini_backend_maps_tier_to_model_and_reads_usage():
    stub = _StubGemini()
    be = GeminiBackend(api_key="k", fast_model="f-model", strong_model="s-model", client=stub)
    r = be.generate_json(system="sys", user="u", json_schema=SCHEMA, tier="strong", max_tokens=99)
    assert stub.seen["model"] == "s-model"
    assert stub.seen["config"].system_instruction == "sys"
    assert stub.seen["config"].response_mime_type == "application/json"
    assert r.text == '{"a": "ok"}' and r.input_tokens == 11 and r.output_tokens == 22


def test_gemini_backend_wraps_sdk_errors_as_transient():
    class _Boom(_StubGemini):
        def generate_content(self, **kw):
            raise RuntimeError("503 backend unavailable")

    be = GeminiBackend(api_key="k", fast_model="f", strong_model="s", client=_Boom())
    with pytest.raises(LLMTransientError):
        be.generate_json(system="s", user="u", json_schema=SCHEMA, tier="fast", max_tokens=10)


class _StubOpenAI:
    def __init__(self):
        self.chat = self
        self.completions = self
        self.seen = {}

    def create(self, **kw):
        self.seen = kw

        class M:
            content = '{"a": "ok"}'

        class C:
            message = M()

        class U:
            prompt_tokens, completion_tokens = 5, 6

        class R:
            choices, usage = [C()], U()

        return R()


def test_openai_backend_uses_strict_json_schema_response_format():
    stub = _StubOpenAI()
    be = OpenAIBackend(api_key="k", fast_model="f", strong_model="s", client=stub)
    r = be.generate_json(system="sys", user="u", json_schema=SCHEMA, tier="fast", max_tokens=99)
    rf = stub.seen["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert stub.seen["model"] == "f"
    assert stub.seen["messages"][0] == {"role": "system", "content": "sys"}
    assert stub.seen["max_completion_tokens"] == 99
    assert r.input_tokens == 5 and r.output_tokens == 6


class _Boom:
    name = "boom"

    def generate_json(self, **kw):
        raise LLMTransientError("rate limited")


def test_fallback_switches_on_transient_error():
    secondary = FakeBackend(['{"a": "from-secondary"}'])
    be = FallbackBackend(_Boom(), secondary)
    out = be.generate_json(system="s", user="u", json_schema=SCHEMA, tier="fast", max_tokens=10)
    assert out.text == '{"a": "from-secondary"}'
    assert len(secondary.calls) == 1
    assert be.name == "boom->fake"


def test_fallback_does_not_mask_a_secondary_failure():
    be = FallbackBackend(_Boom(), _Boom())
    with pytest.raises(LLMTransientError):
        be.generate_json(system="s", user="u", json_schema=SCHEMA, tier="fast", max_tokens=10)


def test_factory_rejects_unknown_backend():
    from bqpp.config import load_settings
    from bqpp.llm.factory import build_client

    bad = load_settings().model_copy(deep=True)
    bad.llm.backend = "cohere"
    with pytest.raises(ValueError, match="cohere"):
        build_client(bad)


def test_missing_api_key_gives_an_actionable_error(monkeypatch):
    from bqpp.llm.base import LLMError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match=r"\.env"):
        GeminiBackend(api_key=None, fast_model="f", strong_model="s")
