"""OpenAI backend (openai SDK, Chat Completions + strict json_schema response format).

Chat Completions rather than the Responses API: this layer takes a raw JSON Schema
dict (spec §9.1), and `response_format={"type": "json_schema", ...}` is the
documented path for that shape.
"""

from __future__ import annotations

import os
import time
from typing import Any

from bqpp.llm.base import LLMResponse, LLMTransientError, ModelTier


class OpenAIBackend:
    name = "openai"

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
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        tier: ModelTier,
        max_tokens: int,
    ) -> LLMResponse:
        model = self._models[tier]
        started = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # strict mode requires additionalProperties:false and every property
                # present in `required` — the pipeline schemas are authored that way.
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "bqpp_result",
                        "schema": json_schema,
                        "strict": True,
                    },
                },
                max_completion_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMTransientError(f"openai call failed: {exc}") from exc
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
