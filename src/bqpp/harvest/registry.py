"""Loads config/sources.yaml into typed entries."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from bqpp.config import CONFIG_DIR


class SourceEntry(BaseModel):
    id: str
    adapter: str
    params: dict


def load_sources(path: Path | None = None) -> list[SourceEntry]:
    path = path or CONFIG_DIR / "sources.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = []
    for item in raw["sources"]:
        params = {k: v for k, v in item.items() if k not in ("id", "adapter")}
        entries.append(SourceEntry(id=item["id"], adapter=item["adapter"], params=params))
    return entries
