"""Provider-agnostic LLM interface.

Backends are dumb transports: no retries, no validation, no logging. Everything
policy-shaped lives in `client.LLMClient`, so adding a provider is a ~40-line file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

ModelTier = Literal["fast", "strong"]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0


class LLMError(RuntimeError):
    """Unrecoverable LLM failure — the caller must not store anything."""


class LLMTransientError(LLMError):
    """Retryable failure (timeout, rate limit, 5xx). Triggers fallback if configured."""


class LLMValidationError(LLMError):
    """Model output failed JSON parsing or schema validation after all attempts."""


@runtime_checkable
class LLMBackend(Protocol):
    name: str

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        tier: ModelTier,
        max_tokens: int,
    ) -> LLMResponse: ...
