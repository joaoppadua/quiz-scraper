"""The one place where LLM output is validated, retried and logged."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import jsonschema

from bqpp.llm.base import (
    LLMBackend,
    LLMResponse,
    LLMTransientError,
    LLMValidationError,
    ModelTier,
)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    """Models sometimes wrap JSON in a markdown fence despite being told not to."""
    m = _FENCE.match(text)
    return m.group(1) if m else text.strip()


class LLMClient:
    """Wraps a backend with schema validation, bounded retries and call logging.

    `complete_json` is the spec §9.1 contract: it either returns a dict that
    validated against `json_schema`, or raises. It never returns unvalidated output.
    """

    def __init__(self, backend: LLMBackend, *, max_attempts: int = 3, db: Any = None) -> None:
        self.backend = backend
        self.max_attempts = max_attempts
        self.db = db

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        model_tier: ModelTier,
        max_tokens: int = 2048,
        stage: str | None = None,
    ) -> dict:
        prompt = user
        last_error = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            error: str | None = None
            resp: LLMResponse | None = None
            data: dict | None = None
            try:
                resp = self.backend.generate_json(
                    system=system,
                    user=prompt,
                    json_schema=json_schema,
                    tier=model_tier,
                    max_tokens=max_tokens,
                )
                data = json.loads(_strip_fence(resp.text))
                jsonschema.validate(data, json_schema)
            except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
                error = last_error = f"{type(exc).__name__}: {exc}"
            except LLMTransientError as exc:
                error = last_error = f"transient: {exc}"
            finally:
                self._log(stage, model_tier, attempt, resp, error)

            if error is None:
                assert data is not None  # narrowed by the absence of an error
                return data

            prompt = (
                f"{user}\n\n---\n"
                f"Your previous response was invalid and could not be used.\n"
                f"Validation error: {last_error}\n"
                f"Return ONLY a JSON object that satisfies the schema. "
                f"No prose, no code fences."
            )

        raise LLMValidationError(
            f"{self.backend.name} produced invalid output {self.max_attempts}x; "
            f"last error: {last_error}"
        )

    def _log(
        self,
        stage: str | None,
        tier: ModelTier,
        attempt: int,
        resp: LLMResponse | None,
        error: str | None,
    ) -> None:
        if self.db is None:
            return
        self.db.log_llm_call(
            called_at=datetime.now(UTC).isoformat(),
            stage=stage,
            backend=self.backend.name,
            model=getattr(resp, "model", None),
            tier=tier,
            attempt=attempt,
            input_tokens=getattr(resp, "input_tokens", None),
            output_tokens=getattr(resp, "output_tokens", None),
            latency_ms=getattr(resp, "latency_ms", None),
            ok=error is None,
            error=error,
        )
