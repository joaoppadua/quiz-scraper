"""Scripted backend for tests. Never touches the network."""

from __future__ import annotations

from collections.abc import Callable

from bqpp.llm.base import LLMResponse, ModelTier


class FakeBackend:
    """Returns canned responses in order, or delegates to a callable router.

    `responses` as a list is consumed positionally; as a callable it receives the
    recorded call dict and returns the response text (useful when one fake has to
    serve two pipeline stages).
    """

    name = "fake"

    def __init__(self, responses: list[str] | Callable[[dict], str]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        tier: ModelTier,
        max_tokens: int,
    ) -> LLMResponse:
        call = {
            "system": system,
            "user": user,
            "json_schema": json_schema,
            "tier": tier,
            "max_tokens": max_tokens,
        }
        self.calls.append(call)
        if callable(self._responses):
            text = self._responses(call)
        else:
            idx = len(self.calls) - 1
            if idx >= len(self._responses):
                raise AssertionError(
                    f"FakeBackend exhausted after {len(self._responses)} responses"
                )
            text = self._responses[idx]
        return LLMResponse(
            text=text, model=f"fake-{tier}", input_tokens=10, output_tokens=20, latency_ms=1
        )
