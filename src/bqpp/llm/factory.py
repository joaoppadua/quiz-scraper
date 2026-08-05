"""Build the configured LLMClient from settings + environment."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from bqpp.config import Settings
from bqpp.llm.base import LLMBackend
from bqpp.llm.client import LLMClient
from bqpp.llm.fallback import FallbackBackend


def _backend(name: str, fast: str, strong: str) -> LLMBackend:
    if name == "gemini":
        from bqpp.llm.gemini_client import GeminiBackend

        return GeminiBackend(os.getenv("GEMINI_API_KEY"), fast, strong)
    if name == "openai":
        from bqpp.llm.openai_client import OpenAIBackend

        return OpenAIBackend(os.getenv("OPENAI_API_KEY"), fast, strong)
    raise ValueError(f"unknown LLM backend: {name!r} (expected 'gemini' or 'openai')")


def build_client(settings: Settings, *, db: Any = None) -> LLMClient:
    load_dotenv()
    cfg = settings.llm
    backend = _backend(cfg.backend, cfg.fast_model, cfg.strong_model)
    if cfg.fallback_backend:
        secondary = _backend(
            cfg.fallback_backend,
            cfg.fallback_fast_model or cfg.fast_model,
            cfg.fallback_strong_model or cfg.strong_model,
        )
        backend = FallbackBackend(backend, secondary)
    return LLMClient(backend, max_attempts=cfg.max_attempts, db=db)
