"""Primary → secondary backend wrapper.

Only *transient* failures fall through. A schema violation is not a provider
problem, so it stays with the primary and is retried by LLMClient instead.
"""

from __future__ import annotations

import logging

from bqpp.llm.base import LLMBackend, LLMResponse, LLMTransientError, ModelTier

log = logging.getLogger(__name__)


class FallbackBackend:
    def __init__(self, primary: LLMBackend, secondary: LLMBackend) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = f"{primary.name}->{secondary.name}"

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        tier: ModelTier,
        max_tokens: int,
    ) -> LLMResponse:
        try:
            return self.primary.generate_json(
                system=system, user=user, json_schema=json_schema,
                tier=tier, max_tokens=max_tokens,
            )
        except LLMTransientError as exc:
            log.warning(
                "primary backend %s failed (%s); falling back to %s",
                self.primary.name, exc, self.secondary.name,
            )
            return self.secondary.generate_json(
                system=system, user=user, json_schema=json_schema,
                tier=tier, max_tokens=max_tokens,
            )
