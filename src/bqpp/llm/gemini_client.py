"""Google Gemini backend (google-genai SDK).

Model ids come from config/settings.toml, never from this file — the Gemini line
moves fast and a hardcoded id becomes a silent 404 months later.
"""

from __future__ import annotations

import os
import time
from typing import Any

from bqpp.llm.base import LLMResponse, LLMTransientError, ModelTier

# Gemini's `response_schema` accepts a JSON-Schema *subset*; these keys are rejected.
# OpenAI strict mode, by contrast, *requires* additionalProperties — so the schemas
# in classify.py / vet.py are authored for OpenAI and trimmed here for Gemini.
_UNSUPPORTED = {"additionalProperties", "$schema", "$id", "definitions", "$defs", "default"}


def to_gemini_schema(schema: Any) -> Any:
    """Recursively drop JSON-Schema keys Gemini's structured-output subset rejects."""
    if isinstance(schema, dict):
        return {k: to_gemini_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED}
    if isinstance(schema, list):
        return [to_gemini_schema(v) for v in schema]
    return schema


class GeminiBackend:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None,
        fast_model: str,
        strong_model: str,
        *,
        client: Any = None,
    ) -> None:
        self._models = {"fast": fast_model, "strong": strong_model}
        if client is not None:
            self._client = client  # test seam
        else:
            from google import genai

            self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        tier: ModelTier,
        max_tokens: int,
    ) -> LLMResponse:
        from google.genai import types

        model = self._models[tier]
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=to_gemini_schema(json_schema),
            max_output_tokens=max_tokens,
        )
        started = time.perf_counter()
        try:
            resp = self._client.models.generate_content(
                model=model, contents=user, config=config
            )
        except Exception as exc:  # google.genai.errors.APIError + transport errors
            raise LLMTransientError(f"gemini call failed: {exc}") from exc
        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text=resp.text or "",
            model=model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
