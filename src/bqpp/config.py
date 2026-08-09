"""Loading and validation of the on-disk configuration files."""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class LLMSettings(BaseModel):
    backend: str = "gemini"
    fallback_backend: str = ""
    fast_model: str
    strong_model: str
    fallback_fast_model: str = ""
    fallback_strong_model: str = ""
    max_attempts: int = 3
    max_tokens: int = 2048
    requests_per_second: float = 2.0


class RankingSettings(BaseModel):
    format_weights: dict[str, float] = Field(default_factory=dict)
    vet_ok_bonus: float = 2.0
    rationale_bonus: float = 1.5
    year_weight: float = 0.05
    shortlist_size: int = 5


class HarvestSettings(BaseModel):
    user_agent: str


class Settings(BaseModel):
    data_dir: Path
    raw_dir: Path
    db_path: Path
    export_dir: Path
    shortlist_dir: Path
    llm: LLMSettings
    ranking: RankingSettings
    harvest: HarvestSettings

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.export_dir, self.shortlist_dir):
            p.mkdir(parents=True, exist_ok=True)


class Taxonomy(BaseModel):
    discipline: str
    labels: dict[str, str]
    topic_of: dict[str, str]
    topic_labels: dict[str, str]
    # subtopic id -> how it is opened when no exam question serves it, e.g. "doutrina"
    opens_with: dict[str, str] = Field(default_factory=dict)

    @property
    def subtopic_ids(self) -> set[str]:
        return set(self.labels)

    def validate_ids(self, ids: list[str]) -> list[str]:
        """Return the subset of `ids` that are NOT valid subtopic ids."""
        return [i for i in ids if i not in self.labels]

    def as_prompt_yaml(self) -> str:
        """The taxonomy rendered for prompt interpolation (stable ordering)."""
        lines = [f"discipline: {self.discipline}", "subtopics:"]
        for sid, label in self.labels.items():
            lines.append(f"  - id: {sid}\n    label: {label}")
        return "\n".join(lines)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


@cache
def load_settings(path: Path | None = None) -> Settings:
    path = path or CONFIG_DIR / "settings.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    paths = raw["paths"]
    return Settings(
        data_dir=_resolve(PROJECT_ROOT, paths["data_dir"]),
        raw_dir=_resolve(PROJECT_ROOT, paths["raw_dir"]),
        db_path=_resolve(PROJECT_ROOT, paths["db_path"]),
        export_dir=_resolve(PROJECT_ROOT, paths["export_dir"]),
        shortlist_dir=_resolve(PROJECT_ROOT, paths["shortlist_dir"]),
        llm=LLMSettings(**raw["llm"]),
        ranking=RankingSettings(**raw["ranking"]),
        harvest=HarvestSettings(**raw["harvest"]),
    )


@cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    path = path or CONFIG_DIR / "taxonomy.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    labels: dict[str, str] = {}
    topic_of: dict[str, str] = {}
    topic_labels: dict[str, str] = {}
    opens_with: dict[str, str] = {}
    for topic in raw["topics"]:
        topic_labels[topic["id"]] = topic["label"]
        for sub in topic["subtopics"]:
            labels[sub["id"]] = sub["label"]
            topic_of[sub["id"]] = topic["id"]
            if sub.get("opens_with"):
                opens_with[sub["id"]] = sub["opens_with"]
    return Taxonomy(
        discipline=raw["discipline"],
        labels=labels,
        topic_of=topic_of,
        topic_labels=topic_labels,
        opens_with=opens_with,
    )
