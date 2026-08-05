# banco-questoes-pp — M0 + M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the corpus builder and semester curation pipeline described in `SPEC-questoes-pipeline.md`, scoped to milestones M0 (skeleton) and M1 (HuggingFace bootstrap end-to-end), producing markdown shortlists of vetted *processo penal* exam questions per subtopic.

**Architecture:** A CLI-driven, stage-based pipeline. Each stage (`harvest → classify → vet → curate`) is idempotent and communicates only through a SQLite database and an on-disk file cache, so any stage can be re-run in isolation. The LLM layer is a thin `LLMBackend` protocol with pluggable implementations (Gemini primary, OpenAI fallback, a fake for tests); every LLM call is schema-validated client-side and retried on invalid output.

**Tech Stack:** Python 3.11+ · pydantic v2 · typer · SQLite (WAL) · PyYAML · jsonschema · huggingface_hub + pyarrow · google-genai · openai · pytest · uv

---

## Global Constraints

Copied verbatim from the spec; every task's requirements implicitly include this section.

- **Python ≥ 3.11.** Code, identifiers and docs in English; domain terms and all question content in **pt-BR**.
- **Provider-agnostic LLM layer.** No hard dependency on a single LLM vendor; a thin interface with pluggable backends.
- **Only public, official (or officially mirrored) sources.** No scraping of paywalled commercial aggregators (QConcursos, TEC Concursos, etc.).
- **Human-in-the-loop.** The pipeline never decides which question is used in class; it shortlists, the professor chooses.
- **Small scale, high reliability.** Prefer correctness and provenance over volume.
- **Verbatim text, never paraphrased.** Every shortlist entry must carry full attribution (banca, certame, year, URL).
- Every stage is **idempotent and re-runnable**; work is keyed by deterministic IDs and existing rows are skipped unless `--force`.
- Every CLI command supports `--dry-run` and `-v`.
- `complete_json` MUST validate the response against `json_schema` and retry up to 3 times on invalid JSON (re-prompting with the validation error). Fail loudly after retries; **never store unvalidated output.**
- All LLM calls log: model, tier, token counts, latency.
- `data/` is gitignored. `shortlists/` is committed.

---

## Spec Amendments (verified against live sources, 2026-08-05)

These correct §6 of the spec. **Implement the amended values, not the spec's.**

| # | Spec says | Reality | Consequence |
|---|---|---|---|
| A1 | `eduagarcia/oab_exams` → `subject_filter: ["Criminal Procedure"]` | The column is `question_type` and the value is **`CRIMINAL-PROCEDURE`** (87 rows). There is also `CRIMINAL` (90 rows). | Filter on `["CRIMINAL-PROCEDURE", "CRIMINAL"]` and let the classify stage decide discipline — `CRIMINAL` items frequently turn on procedural reasoning (`mixed`). |
| A2 | `maritaca-ai/oab-bench` → `subject_filter: ["Criminal"]` | The column is `category`, formatted `{exam_number}_{discipline}`, e.g. `39_direito_penal`. **30 rows** are `*_direito_penal`. | Filter with a suffix match on `direito_penal`. |
| A3 | oab-bench "includes examiner guidelines" — shape unstated | Guidelines live in a **separate config** (`guidelines`), joined on `question_id`, where `model_id == "guidelines"` and the text is at `choices[0].turns[i]` (one entry per sub-item). | Load both configs; join to populate `answer_rationale`. |
| A4 | oab_exams is "2,210 OAB 1ª fase questions" | 2,210 is the **all-subjects** total; the criminal slice is 87 + 90 = **177**. | Corpus scale for M1 is ~200 raw items, not ~2,000. Cost envelope (§9.2) is an order of magnitude smaller. |
| A5 | — | Every `oab_exams` item is **2010–2018**, i.e. entirely pre-*Pacote Anticrime* (2020-01-23). | The `law_watchlist` vetting is load-bearing, not decorative. Expect a high `desatualizada` / `resposta_mudou_mas_util` rate on T1.x, T2.4, T3.4, T3.5. |

**Additional data-shape facts** (used by Task 7):

- `oab_exams` columns: `id` (str), `question_number` (int32), `exam_id` (str, e.g. `2010-01`), `exam_year` (str), `question_type` (str), `nullified` (bool), `question` (str), `choices` (struct-of-lists: `{"text": [...], "label": [...]}`), `answerKey` (str).
- `oab-bench` / `questions` columns: `question_id` (str), `category` (str), `statement` (str — the stem), `turns` (list[str] — sub-items; `[""]` for a peça), `values` (list[float] — point values), `system` (str).
- A `question_id` ending in `_peca_profissional` is a **peça** → `format = "peca"`, store stem only (spec §2 out-of-scope for internal structure).

**Risk to surface, not to solve here:** with ~20 subtopics × 3–5 candidates, the M1 definition of done ("≥15 subtopics populated") is plausible but not guaranteed from ~200 raw items. `bqpp stats` must therefore report per-subtopic coverage so thin subtopics are visible before M2.

---

## LLM Backend Decision (supersedes spec §9)

The spec names `anthropic_client.py` as the backend to implement first. The professor has since specified **Gemini as the production primary and OpenAI as the fallback** on cost grounds. Implement:

| Backend | Module | Tier `fast` | Tier `strong` |
|---|---|---|---|
| Gemini (primary) | `llm/gemini_client.py` | `gemini-3.5-flash-lite` | `gemini-3.6-flash` |
| OpenAI (fallback) | `llm/openai_client.py` | `gpt-5.4-mini` | `gpt-5.5` |
| Fake (tests only) | `llm/fake_client.py` | — | — |

Model IDs are **config values in `settings.toml`, never literals in code** — they drift fast (the Gemini defaults above are GA as of 2026-08; `gemini-3.1-pro-preview` is available for `strong` if the professor wants Pro-tier vetting and accepts preview rate limits). An Anthropic backend remains a ~40-line addition against the same protocol if ever wanted.

Provider API shapes (verified 2026-08-05):

- **Gemini** — `google-genai` (package `google-genai`, `from google import genai`). Call: `client.models.generate_content(model=..., contents=user, config=types.GenerateContentConfig(system_instruction=..., response_mime_type="application/json", response_schema=..., max_output_tokens=...))`. Text at `response.text`; usage at `response.usage_metadata`. **Gemini's schema subset rejects `additionalProperties`/`$schema`** → strip them before sending (Task 5).
- **OpenAI** — `openai` ≥2.x. Use `client.chat.completions.create(..., response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": True}}, max_completion_tokens=...)`. Text at `resp.choices[0].message.content`; usage at `resp.usage`. **Strict mode requires `additionalProperties: false` and every property listed in `required`** → author schemas that way (Task 8/9).

Because the two providers accept overlapping-but-different schema subsets, **client-side `jsonschema` validation is always authoritative** and runs on every response regardless of backend.

---

## File Structure

```
quiz-scraper/
├── SPEC-questoes-pipeline.md        # existing
├── README.md                        # T11
├── pyproject.toml                   # T1
├── .gitignore  .env.example         # T1
├── config/
│   ├── settings.toml                # T1  paths, backend selection, model names, ranking weights
│   ├── taxonomy.yaml                # T1  canonical subtopics (spec §7, verbatim)
│   ├── sources.yaml                 # T7  source registry (amended per A1/A2)
│   └── law_watchlist.yaml           # T9  law/jurisprudence change watchlist
├── prompts/
│   ├── classify.md                  # T8
│   └── vet.md                       # T9
├── src/bqpp/
│   ├── __init__.py                  # T1
│   ├── config.py                    # T1  Settings/Taxonomy/Watchlist loaders
│   ├── models.py                    # T2  pydantic models + deterministic IDs
│   ├── db.py                        # T3  the ONLY module that touches SQL
│   ├── llm/
│   │   ├── __init__.py              # T4
│   │   ├── base.py                  # T4  LLMBackend protocol, LLMResponse, errors
│   │   ├── fake_client.py           # T4  scripted test double
│   │   ├── client.py                # T4  LLMClient.complete_json — validate + retry + log
│   │   ├── gemini_client.py         # T5
│   │   ├── openai_client.py         # T5
│   │   ├── fallback.py              # T5  primary → fallback wrapper
│   │   └── factory.py               # T5  build_client(settings)
│   ├── harvest/
│   │   ├── __init__.py  registry.py # T7
│   │   └── hf_datasets.py           # T7
│   ├── classify.py                  # T8
│   ├── vet.py                       # T9
│   ├── curate.py                    # T10
│   ├── export.py                    # T11
│   └── cli.py                       # T6 (skeleton) → extended by T7–T11
├── data/                            # gitignored: raw/, corpus.sqlite, export/
├── shortlists/                      # committed curation output
└── tests/
    ├── conftest.py  fixtures/  test_*.py
```

Responsibility boundaries that matter: **`db.py` is the only module that emits SQL**; **`llm/client.py` is the only place retries and validation live** (backends are dumb transports); **`cli.py` holds no business logic** — it parses args and calls stage functions.

---

## Task 1: Project skeleton, settings, and taxonomy

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `config/settings.toml`, `config/taxonomy.yaml`, `src/bqpp/__init__.py`, `src/bqpp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (pydantic), `Taxonomy` with `.subtopic_ids: set[str]`, `.labels: dict[str, str]`, `.validate_ids(ids) -> list[str]`; loaders `load_settings(path=None) -> Settings`, `load_taxonomy(path=None) -> Taxonomy`. `Settings` fields used later: `.db_path`, `.raw_dir`, `.export_dir`, `.shortlist_dir`, `.llm.backend`, `.llm.fallback_backend`, `.llm.fast_model`, `.llm.strong_model`, `.llm.max_attempts`, `.ranking.format_weights`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from bqpp.config import load_settings, load_taxonomy

def test_taxonomy_loads_all_spec_subtopics():
    tax = load_taxonomy()
    assert tax.discipline == "direito-processual-penal"
    assert len(tax.subtopic_ids) == 20
    assert {"T1.1", "T2.9", "T3.5", "T4.3"} <= tax.subtopic_ids
    assert tax.labels["T1.3"] == "Prisão temporária"

def test_validate_ids_rejects_unknown():
    tax = load_taxonomy()
    assert tax.validate_ids(["T1.1", "T9.9", "T2.4"]) == ["T9.9"]

def test_settings_defaults_and_llm_block():
    s = load_settings()
    assert s.llm.backend == "gemini"
    assert s.llm.fallback_backend == "openai"
    assert s.llm.max_attempts == 3
    assert s.db_path.name == "corpus.sqlite"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bqpp'`

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "banco-questoes-pp"
version = "0.1.0"
description = "Exam-question corpus builder and semester curation pipeline for Processo Penal 2 (UFF)"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "rich>=13.7",
    "pyyaml>=6.0",
    "jsonschema>=4.21",
    "python-dotenv>=1.0",
    "huggingface-hub>=0.23",
    "pyarrow>=16.0",
    "google-genai>=1.0",
    "openai>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "pytest-cov>=5.0", "ruff>=0.4"]

[project.scripts]
bqpp = "bqpp.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/bqpp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 4: Create `config/taxonomy.yaml`** — copy §7 of the spec verbatim (20 subtopics across T1–T4). Keep labels in pt-BR exactly as written, including the quoted `"\"Teoria\" dos recursos no processo penal"`.

- [ ] **Step 5: Create `config/settings.toml`**

```toml
[paths]
data_dir      = "data"
raw_dir       = "data/raw"
db_path       = "data/corpus.sqlite"
export_dir    = "data/export"
shortlist_dir = "shortlists"

[llm]
backend          = "gemini"
fallback_backend = "openai"      # "" to disable fallback
fast_model       = "gemini-3.5-flash-lite"
strong_model     = "gemini-3.6-flash"
fallback_fast_model   = "gpt-5.4-mini"
fallback_strong_model = "gpt-5.5"
max_attempts     = 3
max_tokens       = 2048
requests_per_second = 2.0

[ranking]
# open formats fit the teaching method best (spec §11.2)
format_weights = { dissertativa = 3.0, certo_errado = 2.0, mcq5 = 1.0, mcq4 = 1.0, peca = 0.0 }
vet_ok_bonus       = 2.0
rationale_bonus    = 1.5
year_weight        = 0.05
shortlist_size     = 5

[harvest]
user_agent = "banco-questoes-pp/0.1 (UFF Faculdade de Direito; teaching corpus; contact: jpcvpadua@gmail.com)"
```

- [ ] **Step 6: Create `.gitignore` and `.env.example`**

```gitignore
# .gitignore
data/
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
.ruff_cache/
```

```bash
# .env.example
GEMINI_API_KEY=
OPENAI_API_KEY=
```

- [ ] **Step 7: Implement `src/bqpp/config.py`**

```python
"""Loading and validation of the on-disk configuration files."""
from __future__ import annotations

import tomllib
from functools import lru_cache
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

    @property
    def subtopic_ids(self) -> set[str]:
        return set(self.labels)

    def validate_ids(self, ids: list[str]) -> list[str]:
        """Return the subset of `ids` that are NOT valid subtopic ids."""
        return [i for i in ids if i not in self.labels]

    def as_prompt_yaml(self) -> str:
        lines = [f"discipline: {self.discipline}", "subtopics:"]
        for sid, label in self.labels.items():
            lines.append(f"  - id: {sid}\n    label: {label}")
        return "\n".join(lines)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


@lru_cache(maxsize=None)
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


@lru_cache(maxsize=None)
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    path = path or CONFIG_DIR / "taxonomy.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    labels: dict[str, str] = {}
    topic_of: dict[str, str] = {}
    topic_labels: dict[str, str] = {}
    for topic in raw["topics"]:
        topic_labels[topic["id"]] = topic["label"]
        for sub in topic["subtopics"]:
            labels[sub["id"]] = sub["label"]
            topic_of[sub["id"]] = topic["id"]
    return Taxonomy(
        discipline=raw["discipline"],
        labels=labels,
        topic_of=topic_of,
        topic_labels=topic_labels,
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore .env.example config/ src/bqpp/__init__.py src/bqpp/config.py tests/test_config.py
git commit -m "feat: project skeleton, settings and taxonomy loaders"
```

---

## Task 2: Domain models and deterministic IDs

**Files:**
- Create: `src/bqpp/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SourceDocument`, `Question`, `UsageEntry`, `VetReason`; enums-as-`Literal` types `Format`, `VetStatus`, `Discipline`, `Carreira`, `Kind`; helpers `source_doc_id(payload: bytes) -> str` and `question_id(source_doc_id: str, question_number: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from bqpp.models import Question, SourceDocument, question_id, source_doc_id

def test_ids_are_deterministic_and_distinct():
    a = source_doc_id(b"hello")
    assert a == source_doc_id(b"hello") and len(a) == 64
    q1 = question_id(a, "12")
    assert q1 == question_id(a, "12")
    assert q1 != question_id(a, "13")

def test_question_choices_roundtrip():
    q = Question(
        id="x", source_doc_id="y", question_number="1", format="mcq4",
        stem="Enunciado", choices=[{"label": "A", "text": "alt A"}], answer_key="A",
    )
    assert q.choices[0]["label"] == "A"
    assert q.subtopic_ids == []
    assert q.vet_status == "unvetted"
    assert q.nullified is False

def test_source_document_requires_provenance():
    d = SourceDocument(
        id="abc", source_id="hf-oab-exams", url="https://hf.co/x",
        fetched_at="2026-08-05T10:00:00Z", kind="dataset", banca="FGV",
        carreira="oab", certame="OAB 2010-01", exam_year=2010,
    )
    assert d.banca == "FGV" and d.exam_year == 2010
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_models.py -v` — Expected: FAIL, `No module named 'bqpp.models'`

- [ ] **Step 3: Implement `src/bqpp/models.py`**

```python
"""Pydantic models mirroring the SQLite schema (spec §8)."""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["prova", "gabarito", "gabarito_justificado", "dataset"]
Format = Literal["mcq4", "mcq5", "certo_errado", "dissertativa", "peca"]
Discipline = Literal["direito-processual-penal", "other", "mixed"]
Carreira = Literal["oab", "magistratura", "mp", "delegado", "defensoria", "outra"]
VetStatus = Literal["unvetted", "ok", "flagged", "rejected"]
Difficulty = Literal["easy", "medium", "hard"]


def source_doc_id(payload: bytes) -> str:
    """Deterministic id for a source document: sha256 of its bytes."""
    return hashlib.sha256(payload).hexdigest()


def question_id(source_doc_id: str, question_number: str) -> str:
    """Deterministic id for a question: sha256(source_doc_id + question_number)."""
    return hashlib.sha256(f"{source_doc_id}:{question_number}".encode("utf-8")).hexdigest()


class SourceDocument(BaseModel):
    id: str
    source_id: str
    url: str
    fetched_at: str            # ISO 8601
    kind: Kind
    banca: str | None = None
    carreira: Carreira | None = None
    certame: str | None = None
    exam_year: int | None = None
    local_path: str | None = None


class VetReason(BaseModel):
    code: str
    detail: str


class Question(BaseModel):
    id: str
    source_doc_id: str
    question_number: str | None = None
    format: Format
    stem: str
    choices: list[dict[str, str]] | None = None
    answer_key: str | None = None
    answer_rationale: str | None = None
    nullified: bool = False
    # classification stage
    discipline: Discipline | None = None
    subtopic_ids: list[str] = Field(default_factory=list)
    difficulty: Difficulty | None = None
    classified_note: str | None = None
    classify_model: str | None = None
    classified_at: str | None = None
    # vetting stage
    vet_status: VetStatus = "unvetted"
    vet_reasons: list[VetReason] = Field(default_factory=list)
    pedagogy_note: str | None = None
    vet_model: str | None = None
    vetted_at: str | None = None

    def primary_subtopic(self) -> str | None:
        return self.subtopic_ids[0] if self.subtopic_ids else None

    def to_prompt_payload(self) -> dict[str, Any]:
        """The question as handed to the LLM — content only, no pipeline metadata."""
        return {
            "format": self.format,
            "stem": self.stem,
            "choices": self.choices,
            "answer_key": self.answer_key,
        }


class UsageEntry(BaseModel):
    question_id: str
    semester: str
    subtopic_id: str
    used_at: str | None = None
    note: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v` — Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/bqpp/models.py tests/test_models.py
git commit -m "feat: domain models and deterministic id helpers"
```

---

## Task 3: SQLite access layer

**Files:**
- Create: `src/bqpp/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `bqpp.models` (`Question`, `SourceDocument`, `UsageEntry`).
- Produces: class `Database` with `connect(path) -> Database` (classmethod), `.init_schema()`, `.upsert_source_document(doc, force=False) -> bool`, `.upsert_question(q, force=False) -> bool`, `.get_question(qid) -> Question | None`, `.iter_questions(*, unclassified=False, unvetted=False, subtopic=None) -> Iterator[Question]`, `.update_classification(qid, **fields)`, `.update_vetting(qid, **fields)`, `.record_usage(entry)`, `.used_question_ids(before_semester=None) -> set[str]`, `.stats() -> dict`, `.log_llm_call(...)`, `.close()`. Boolean returns are `True` when a row was written, `False` when skipped as already-present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import pytest
from bqpp.db import Database
from bqpp.models import Question, SourceDocument, UsageEntry

@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()

def _doc(i="d1"):
    return SourceDocument(id=i, source_id="hf-oab-exams", url="u", fetched_at="2026-08-05T00:00:00Z",
                          kind="dataset", banca="FGV", carreira="oab", certame="OAB 2010-01", exam_year=2010)

def _q(i="q1", doc="d1", fmt="mcq4"):
    return Question(id=i, source_doc_id=doc, question_number="1", format=fmt, stem="Enunciado",
                    choices=[{"label": "A", "text": "a"}], answer_key="A")

def test_upsert_is_idempotent(db):
    db.upsert_source_document(_doc())
    assert db.upsert_question(_q()) is True
    assert db.upsert_question(_q()) is False          # already present, skipped
    assert db.upsert_question(_q(), force=True) is True
    assert db.stats()["total"] == 1

def test_roundtrip_preserves_json_columns(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q())
    db.update_classification("q1", discipline="direito-processual-penal",
                             subtopic_ids=["T1.2", "T2.4"], difficulty="medium",
                             classified_note=None, classify_model="m", classified_at="2026-08-05T00:00:00Z")
    got = db.get_question("q1")
    assert got.subtopic_ids == ["T1.2", "T2.4"]
    assert got.discipline == "direito-processual-penal"

def test_iter_filters(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q("a")); db.upsert_question(_q("b"))
    db.update_classification("a", discipline="other", subtopic_ids=[], difficulty="easy",
                             classified_note="n", classify_model="m", classified_at="t")
    assert [q.id for q in db.iter_questions(unclassified=True)] == ["b"]

def test_usage_log_blocks_reuse(db):
    db.upsert_source_document(_doc())
    db.upsert_question(_q())
    db.record_usage(UsageEntry(question_id="q1", semester="2026.1", subtopic_id="T1.2", used_at="t"))
    assert db.used_question_ids() == {"q1"}

def test_stats_on_empty_db(tmp_path):
    d = Database.connect(tmp_path / "e.sqlite"); d.init_schema()
    s = d.stats()
    assert s["total"] == 0 and s["by_vet_status"] == {} and s["by_subtopic"] == {}
    d.close()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_db.py -v` — Expected: FAIL, `No module named 'bqpp.db'`

- [ ] **Step 3: Implement `src/bqpp/db.py`**

Use the DDL from spec §8 verbatim, plus `pedagogy_note TEXT` on `questions` (the vet prompt returns it and the shortlist renders it) and an `llm_calls` table (spec §9.1 requires call logging). JSON columns (`choices`, `subtopic_ids`, `vet_reasons`) are `json.dumps`/`json.loads` at this boundary and nowhere else.

```python
"""The only module in the project that emits SQL."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bqpp.models import Question, SourceDocument, UsageEntry, VetReason

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,
  fetched_at TEXT NOT NULL, kind TEXT NOT NULL, banca TEXT, carreira TEXT,
  certame TEXT, exam_year INTEGER, local_path TEXT
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  source_doc_id TEXT NOT NULL REFERENCES source_documents(id),
  question_number TEXT, format TEXT NOT NULL, stem TEXT NOT NULL,
  choices TEXT, answer_key TEXT, answer_rationale TEXT,
  nullified INTEGER DEFAULT 0,
  discipline TEXT, subtopic_ids TEXT, difficulty TEXT, classified_note TEXT,
  classify_model TEXT, classified_at TEXT,
  vet_status TEXT DEFAULT 'unvetted', vet_reasons TEXT, pedagogy_note TEXT,
  vet_model TEXT, vetted_at TEXT
);
CREATE TABLE IF NOT EXISTS usage_log (
  question_id TEXT REFERENCES questions(id), semester TEXT NOT NULL,
  subtopic_id TEXT NOT NULL, used_at TEXT, note TEXT,
  PRIMARY KEY (question_id, semester)
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, called_at TEXT NOT NULL, stage TEXT,
  backend TEXT, model TEXT, tier TEXT, attempt INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, ok INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_vet ON questions(vet_status);
CREATE INDEX IF NOT EXISTS idx_q_discipline ON questions(discipline);
"""


class Database:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    @classmethod
    def connect(cls, path: Path) -> "Database":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn)

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- writes -------------------------------------------------------
    def _exists(self, table: str, row_id: str) -> bool:
        cur = self.conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,))
        return cur.fetchone() is not None

    def upsert_source_document(self, doc: SourceDocument, force: bool = False) -> bool:
        if self._exists("source_documents", doc.id) and not force:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO source_documents "
            "(id, source_id, url, fetched_at, kind, banca, carreira, certame, exam_year, local_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc.id, doc.source_id, doc.url, doc.fetched_at, doc.kind, doc.banca,
             doc.carreira, doc.certame, doc.exam_year, doc.local_path),
        )
        self.conn.commit()
        return True

    def upsert_question(self, q: Question, force: bool = False) -> bool:
        if self._exists("questions", q.id) and not force:
            return False
        self.conn.execute(
            "INSERT OR REPLACE INTO questions "
            "(id, source_doc_id, question_number, format, stem, choices, answer_key, "
            " answer_rationale, nullified, discipline, subtopic_ids, difficulty, classified_note, "
            " classify_model, classified_at, vet_status, vet_reasons, pedagogy_note, vet_model, vetted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (q.id, q.source_doc_id, q.question_number, q.format, q.stem,
             json.dumps(q.choices, ensure_ascii=False) if q.choices else None,
             q.answer_key, q.answer_rationale, int(q.nullified), q.discipline,
             json.dumps(q.subtopic_ids), q.difficulty, q.classified_note,
             q.classify_model, q.classified_at, q.vet_status,
             json.dumps([r.model_dump() for r in q.vet_reasons], ensure_ascii=False),
             q.pedagogy_note, q.vet_model, q.vetted_at),
        )
        self.conn.commit()
        return True

    def update_classification(self, qid: str, **f: Any) -> None:
        self.conn.execute(
            "UPDATE questions SET discipline=?, subtopic_ids=?, difficulty=?, classified_note=?, "
            "classify_model=?, classified_at=? WHERE id=?",
            (f["discipline"], json.dumps(f["subtopic_ids"]), f["difficulty"],
             f["classified_note"], f["classify_model"], f["classified_at"], qid),
        )
        self.conn.commit()

    def update_vetting(self, qid: str, **f: Any) -> None:
        self.conn.execute(
            "UPDATE questions SET vet_status=?, vet_reasons=?, pedagogy_note=?, vet_model=?, "
            "vetted_at=? WHERE id=?",
            (f["vet_status"],
             json.dumps([r.model_dump() for r in f["vet_reasons"]], ensure_ascii=False),
             f.get("pedagogy_note"), f.get("vet_model"), f.get("vetted_at"), qid),
        )
        self.conn.commit()

    def record_usage(self, entry: UsageEntry) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO usage_log (question_id, semester, subtopic_id, used_at, note) "
            "VALUES (?,?,?,?,?)",
            (entry.question_id, entry.semester, entry.subtopic_id, entry.used_at, entry.note),
        )
        self.conn.commit()

    def log_llm_call(self, **f: Any) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls (called_at, stage, backend, model, tier, attempt, "
            "input_tokens, output_tokens, latency_ms, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f["called_at"], f.get("stage"), f.get("backend"), f.get("model"), f.get("tier"),
             f.get("attempt"), f.get("input_tokens"), f.get("output_tokens"),
             f.get("latency_ms"), int(f.get("ok", True)), f.get("error")),
        )
        self.conn.commit()

    # ---- reads --------------------------------------------------------
    @staticmethod
    def _to_question(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"], source_doc_id=row["source_doc_id"],
            question_number=row["question_number"], format=row["format"], stem=row["stem"],
            choices=json.loads(row["choices"]) if row["choices"] else None,
            answer_key=row["answer_key"], answer_rationale=row["answer_rationale"],
            nullified=bool(row["nullified"]), discipline=row["discipline"],
            subtopic_ids=json.loads(row["subtopic_ids"]) if row["subtopic_ids"] else [],
            difficulty=row["difficulty"], classified_note=row["classified_note"],
            classify_model=row["classify_model"], classified_at=row["classified_at"],
            vet_status=row["vet_status"],
            vet_reasons=[VetReason(**r) for r in json.loads(row["vet_reasons"] or "[]")],
            pedagogy_note=row["pedagogy_note"], vet_model=row["vet_model"],
            vetted_at=row["vetted_at"],
        )

    def get_question(self, qid: str) -> Question | None:
        row = self.conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        return self._to_question(row) if row else None

    def get_source_document(self, doc_id: str) -> SourceDocument | None:
        row = self.conn.execute("SELECT * FROM source_documents WHERE id=?", (doc_id,)).fetchone()
        return SourceDocument(**dict(row)) if row else None

    def iter_questions(self, *, unclassified: bool = False, unvetted: bool = False,
                       subtopic: str | None = None) -> Iterator[Question]:
        sql, params = "SELECT * FROM questions WHERE 1=1", []
        if unclassified:
            sql += " AND classified_at IS NULL"
        if unvetted:
            sql += " AND vet_status='unvetted'"
        if subtopic:
            sql += " AND subtopic_ids LIKE ?"
            params.append(f'%"{subtopic}"%')
        sql += " ORDER BY id"
        for row in self.conn.execute(sql, params):
            yield self._to_question(row)

    def used_question_ids(self, before_semester: str | None = None) -> set[str]:
        sql, params = "SELECT question_id FROM usage_log", []
        if before_semester:
            sql += " WHERE semester < ?"
            params.append(before_semester)
        return {r[0] for r in self.conn.execute(sql, params)}

    def stats(self) -> dict[str, Any]:
        def counts(sql: str) -> dict[str, int]:
            return {str(r[0]): r[1] for r in self.conn.execute(sql) if r[0] is not None}

        total = self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        by_subtopic: dict[str, int] = {}
        for row in self.conn.execute("SELECT subtopic_ids FROM questions WHERE subtopic_ids IS NOT NULL"):
            for sid in json.loads(row[0] or "[]"):
                by_subtopic[sid] = by_subtopic.get(sid, 0) + 1
        return {
            "total": total,
            "by_format": counts("SELECT format, COUNT(*) FROM questions GROUP BY format"),
            "by_vet_status": counts("SELECT vet_status, COUNT(*) FROM questions "
                                    "WHERE classified_at IS NOT NULL GROUP BY vet_status"),
            "by_discipline": counts("SELECT discipline, COUNT(*) FROM questions GROUP BY discipline"),
            "by_subtopic": by_subtopic,
            "sources": counts("SELECT source_id, COUNT(*) FROM source_documents GROUP BY source_id"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v` — Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/bqpp/db.py tests/test_db.py
git commit -m "feat: SQLite access layer with idempotent upserts"
```

---

## Task 4: LLM protocol, fake backend, and the validate-and-retry client

**Files:**
- Create: `src/bqpp/llm/__init__.py`, `src/bqpp/llm/base.py`, `src/bqpp/llm/fake_client.py`, `src/bqpp/llm/client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `bqpp.db.Database` (optional, for call logging).
- Produces: `ModelTier = Literal["fast","strong"]`; `LLMResponse(text, model, input_tokens, output_tokens, latency_ms)`; exceptions `LLMError`, `LLMTransientError`, `LLMValidationError`; protocol `LLMBackend` with attribute `name: str` and method `generate_json(*, system, user, json_schema, tier, max_tokens) -> LLMResponse`; `FakeBackend(responses: list[str] | Callable)` with `.calls: list[dict]`; class `LLMClient(backend, *, max_attempts=3, db=None)` exposing **`complete_json(*, system, user, json_schema, model_tier, max_tokens=2048, stage=None) -> dict`** (the spec §9.1 signature).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
import json
import pytest
from bqpp.llm.base import LLMError, LLMResponse
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend

SCHEMA = {
    "type": "object",
    "properties": {"discipline": {"type": "string"}, "subtopic_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["discipline", "subtopic_ids"],
    "additionalProperties": False,
}

def _call(client):
    return client.complete_json(system="s", user="u", json_schema=SCHEMA, model_tier="fast")

def test_valid_first_try():
    be = FakeBackend(['{"discipline": "direito-processual-penal", "subtopic_ids": ["T1.2"]}'])
    assert _call(LLMClient(be))["subtopic_ids"] == ["T1.2"]
    assert len(be.calls) == 1

def test_retries_on_invalid_json_and_reprompts_with_the_error():
    be = FakeBackend(['not json at all',
                      '{"discipline": "direito-processual-penal", "subtopic_ids": ["T1.2"]}'])
    assert _call(LLMClient(be))["discipline"] == "direito-processual-penal"
    assert len(be.calls) == 2
    assert "not json at all" not in be.calls[1]["user"]
    assert "invalid" in be.calls[1]["user"].lower() or "erro" in be.calls[1]["user"].lower()

def test_retries_on_schema_violation():
    be = FakeBackend(['{"discipline": "x"}',                       # missing required key
                      '{"discipline": "other", "subtopic_ids": []}'])
    assert _call(LLMClient(be))["subtopic_ids"] == []
    assert len(be.calls) == 2

def test_fails_loudly_after_max_attempts_and_never_returns_unvalidated():
    be = FakeBackend(['bad', 'still bad', 'nope'])
    with pytest.raises(LLMError):
        _call(LLMClient(be, max_attempts=3))
    assert len(be.calls) == 3

def test_strips_markdown_code_fences():
    be = FakeBackend(['```json\n{"discipline": "other", "subtopic_ids": []}\n```'])
    assert _call(LLMClient(be))["discipline"] == "other"

def test_logs_every_call_to_db(tmp_path):
    from bqpp.db import Database
    db = Database.connect(tmp_path / "t.sqlite"); db.init_schema()
    be = FakeBackend(['bad', '{"discipline": "other", "subtopic_ids": []}'])
    LLMClient(be, db=db).complete_json(system="s", user="u", json_schema=SCHEMA,
                                       model_tier="fast", stage="classify")
    rows = list(db.conn.execute("SELECT stage, attempt, ok FROM llm_calls ORDER BY id"))
    assert [r[1] for r in rows] == [1, 2]
    assert [bool(r[2]) for r in rows] == [False, True]
    db.close()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_llm_client.py -v` — Expected: FAIL, `No module named 'bqpp.llm'`

- [ ] **Step 3: Implement `src/bqpp/llm/base.py`**

```python
"""Provider-agnostic LLM interface. Backends are dumb transports: no retries, no validation."""
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

    def generate_json(self, *, system: str, user: str, json_schema: dict,
                      tier: ModelTier, max_tokens: int) -> LLMResponse: ...
```

- [ ] **Step 4: Implement `src/bqpp/llm/fake_client.py`**

```python
"""Scripted backend for tests. Never touches the network."""
from __future__ import annotations

from collections.abc import Callable

from bqpp.llm.base import LLMResponse, ModelTier


class FakeBackend:
    name = "fake"

    def __init__(self, responses: list[str] | Callable[[dict], str]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def generate_json(self, *, system: str, user: str, json_schema: dict,
                      tier: ModelTier, max_tokens: int) -> LLMResponse:
        call = {"system": system, "user": user, "json_schema": json_schema,
                "tier": tier, "max_tokens": max_tokens}
        self.calls.append(call)
        if callable(self._responses):
            text = self._responses(call)
        else:
            idx = len(self.calls) - 1
            if idx >= len(self._responses):
                raise AssertionError(f"FakeBackend exhausted after {len(self._responses)} responses")
            text = self._responses[idx]
        return LLMResponse(text=text, model=f"fake-{tier}", input_tokens=10,
                           output_tokens=20, latency_ms=1)
```

- [ ] **Step 5: Implement `src/bqpp/llm/client.py`**

```python
"""The one place where LLM output is validated, retried and logged."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import jsonschema

from bqpp.llm.base import LLMBackend, LLMError, LLMTransientError, LLMValidationError, ModelTier

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _FENCE.match(text)
    return m.group(1) if m else text.strip()


class LLMClient:
    """Wraps a backend with schema validation, bounded retries and call logging."""

    def __init__(self, backend: LLMBackend, *, max_attempts: int = 3, db: Any = None) -> None:
        self.backend = backend
        self.max_attempts = max_attempts
        self.db = db

    def complete_json(self, *, system: str, user: str, json_schema: dict,
                      model_tier: ModelTier, max_tokens: int = 2048,
                      stage: str | None = None) -> dict:
        prompt, last_error = user, "unknown"
        for attempt in range(1, self.max_attempts + 1):
            error: str | None = None
            resp = None
            try:
                resp = self.backend.generate_json(system=system, user=prompt,
                                                  json_schema=json_schema, tier=model_tier,
                                                  max_tokens=max_tokens)
                data = json.loads(_strip_fence(resp.text))
                jsonschema.validate(data, json_schema)
            except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
                error = last_error = f"{type(exc).__name__}: {exc}"
            except LLMTransientError as exc:
                error = last_error = f"transient: {exc}"
            finally:
                self._log(stage, attempt, resp, error)

            if error is None:
                return data
            prompt = (
                f"{user}\n\n---\n"
                f"Your previous response was invalid and could not be used.\n"
                f"Validation error: {last_error}\n"
                f"Return ONLY a JSON object that satisfies the schema. No prose, no code fences."
            )

        raise LLMValidationError(
            f"{self.backend.name} produced invalid output {self.max_attempts}x; last error: {last_error}"
        )

    def _log(self, stage: str | None, attempt: int, resp, error: str | None) -> None:
        if self.db is None:
            return
        self.db.log_llm_call(
            called_at=datetime.now(timezone.utc).isoformat(), stage=stage,
            backend=self.backend.name, model=getattr(resp, "model", None),
            tier=None, attempt=attempt,
            input_tokens=getattr(resp, "input_tokens", None),
            output_tokens=getattr(resp, "output_tokens", None),
            latency_ms=getattr(resp, "latency_ms", None),
            ok=error is None, error=error,
        )
```

`src/bqpp/llm/__init__.py` re-exports `LLMClient`, `LLMBackend`, `LLMResponse`, and the error types.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_client.py -v` — Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add src/bqpp/llm tests/test_llm_client.py
git commit -m "feat: provider-agnostic LLM client with schema validation and retries"
```

---

## Task 5: Gemini and OpenAI backends, plus the fallback wrapper

**Files:**
- Create: `src/bqpp/llm/gemini_client.py`, `src/bqpp/llm/openai_client.py`, `src/bqpp/llm/fallback.py`, `src/bqpp/llm/factory.py`
- Test: `tests/test_llm_backends.py`

**Interfaces:**
- Consumes: `bqpp.llm.base`, `bqpp.config.Settings`.
- Produces: `GeminiBackend(api_key, fast_model, strong_model, *, client=None)`, `OpenAIBackend(api_key, fast_model, strong_model, *, client=None)` (the `client=` seam lets tests inject a stub instead of hitting the network), `FallbackBackend(primary, secondary)`, `build_client(settings, *, db=None) -> LLMClient`, and `to_gemini_schema(schema: dict) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_backends.py
import pytest
from bqpp.llm.base import LLMTransientError
from bqpp.llm.fallback import FallbackBackend
from bqpp.llm.fake_client import FakeBackend
from bqpp.llm.gemini_client import GeminiBackend, to_gemini_schema
from bqpp.llm.openai_client import OpenAIBackend

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}},
          "required": ["a"], "additionalProperties": False, "$schema": "http://json-schema.org/draft-07/schema#"}

def test_gemini_schema_strips_unsupported_keys():
    out = to_gemini_schema(SCHEMA)
    assert "additionalProperties" not in out and "$schema" not in out
    assert out["required"] == ["a"] and out["properties"]["a"]["type"] == "string"

class _StubGemini:
    def __init__(self): self.models = self; self.seen = {}
    def generate_content(self, **kw):
        self.seen = kw
        class U: prompt_token_count, candidates_token_count = 11, 22
        class R: text, usage_metadata = '{"a": "ok"}', U()
        return R()

def test_gemini_backend_maps_tier_to_model_and_reads_usage():
    stub = _StubGemini()
    be = GeminiBackend(api_key="k", fast_model="f-model", strong_model="s-model", client=stub)
    r = be.generate_json(system="sys", user="u", json_schema=SCHEMA, tier="strong", max_tokens=99)
    assert stub.seen["model"] == "s-model"
    assert stub.seen["config"].system_instruction == "sys"
    assert r.text == '{"a": "ok"}' and r.input_tokens == 11 and r.output_tokens == 22

class _StubOpenAI:
    def __init__(self): self.chat = self; self.completions = self; self.seen = {}
    def create(self, **kw):
        self.seen = kw
        class M: content = '{"a": "ok"}'
        class C: message = M()
        class U: prompt_tokens, completion_tokens = 5, 6
        class R: choices, usage = [C()], U()
        return R()

def test_openai_backend_uses_strict_json_schema_response_format():
    stub = _StubOpenAI()
    be = OpenAIBackend(api_key="k", fast_model="f", strong_model="s", client=stub)
    r = be.generate_json(system="sys", user="u", json_schema=SCHEMA, tier="fast", max_tokens=99)
    rf = stub.seen["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert stub.seen["model"] == "f"
    assert stub.seen["messages"][0] == {"role": "system", "content": "sys"}
    assert r.input_tokens == 5 and r.output_tokens == 6

class _Boom:
    name = "boom"
    def generate_json(self, **kw): raise LLMTransientError("rate limited")

def test_fallback_switches_on_transient_error():
    secondary = FakeBackend(['{"a": "from-secondary"}'])
    be = FallbackBackend(_Boom(), secondary)
    assert be.generate_json(system="s", user="u", json_schema=SCHEMA,
                            tier="fast", max_tokens=10).text == '{"a": "from-secondary"}'
    assert len(secondary.calls) == 1

def test_fallback_does_not_mask_a_secondary_failure():
    be = FallbackBackend(_Boom(), _Boom())
    with pytest.raises(LLMTransientError):
        be.generate_json(system="s", user="u", json_schema=SCHEMA, tier="fast", max_tokens=10)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_llm_backends.py -v` — Expected: FAIL, `No module named 'bqpp.llm.gemini_client'`

- [ ] **Step 3: Implement `src/bqpp/llm/gemini_client.py`**

```python
"""Google Gemini backend (google-genai SDK)."""
from __future__ import annotations

import os
import time
from typing import Any

from bqpp.llm.base import LLMResponse, LLMTransientError, ModelTier

# Gemini's response_schema accepts a JSON-Schema subset; these keys are rejected.
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

    def __init__(self, api_key: str | None, fast_model: str, strong_model: str,
                 *, client: Any = None) -> None:
        self._models = {"fast": fast_model, "strong": strong_model}
        if client is not None:
            self._client = client
        else:
            from google import genai
            self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])

    def generate_json(self, *, system: str, user: str, json_schema: dict,
                      tier: ModelTier, max_tokens: int) -> LLMResponse:
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
            resp = self._client.models.generate_content(model=model, contents=user, config=config)
        except Exception as exc:  # SDK raises google.genai.errors.APIError and transport errors
            raise LLMTransientError(f"gemini call failed: {exc}") from exc
        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text=resp.text or "",
            model=model,
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
```

- [ ] **Step 4: Implement `src/bqpp/llm/openai_client.py`**

```python
"""OpenAI backend (openai SDK, Chat Completions + strict json_schema response format)."""
from __future__ import annotations

import os
import time
from typing import Any

from bqpp.llm.base import LLMResponse, LLMTransientError, ModelTier


class OpenAIBackend:
    name = "openai"

    def __init__(self, api_key: str | None, fast_model: str, strong_model: str,
                 *, client: Any = None) -> None:
        self._models = {"fast": fast_model, "strong": strong_model}
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])

    def generate_json(self, *, system: str, user: str, json_schema: dict,
                      tier: ModelTier, max_tokens: int) -> LLMResponse:
        model = self._models[tier]
        started = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "bqpp_result",
                                                 "schema": json_schema,
                                                 "strict": True}},
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
```

> **Note for the implementer:** OpenAI strict mode requires `additionalProperties: false` **and** every property listed in `required`. The schemas in Tasks 8 and 9 are authored that way. Gemini rejects `additionalProperties`, which is why `to_gemini_schema` strips it — the same schema object therefore works for both providers.

- [ ] **Step 5: Implement `src/bqpp/llm/fallback.py`**

```python
"""Primary → secondary backend wrapper. Transient failures fall through; others propagate."""
from __future__ import annotations

import logging

from bqpp.llm.base import LLMBackend, LLMResponse, LLMTransientError, ModelTier

log = logging.getLogger(__name__)


class FallbackBackend:
    def __init__(self, primary: LLMBackend, secondary: LLMBackend) -> None:
        self.primary, self.secondary = primary, secondary
        self.name = f"{primary.name}->{secondary.name}"

    def generate_json(self, *, system: str, user: str, json_schema: dict,
                      tier: ModelTier, max_tokens: int) -> LLMResponse:
        try:
            return self.primary.generate_json(system=system, user=user, json_schema=json_schema,
                                              tier=tier, max_tokens=max_tokens)
        except LLMTransientError as exc:
            log.warning("primary backend %s failed (%s); falling back to %s",
                        self.primary.name, exc, self.secondary.name)
            return self.secondary.generate_json(system=system, user=user, json_schema=json_schema,
                                                tier=tier, max_tokens=max_tokens)
```

- [ ] **Step 6: Implement `src/bqpp/llm/factory.py`**

```python
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
        secondary = _backend(cfg.fallback_backend,
                             cfg.fallback_fast_model or cfg.fast_model,
                             cfg.fallback_strong_model or cfg.strong_model)
        backend = FallbackBackend(backend, secondary)
    return LLMClient(backend, max_attempts=cfg.max_attempts, db=db)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_backends.py -v` — Expected: 6 passed

- [ ] **Step 8: Commit**

```bash
git add src/bqpp/llm tests/test_llm_backends.py
git commit -m "feat: Gemini and OpenAI backends with transient-error fallback"
```

---

## Task 6: CLI skeleton — `bqpp stats` on an empty DB (M0 definition of done)

**Files:**
- Create: `src/bqpp/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `bqpp.config.load_settings`, `bqpp.db.Database`.
- Produces: `app` (typer app) with commands `stats`, `harvest`, `classify`, `vet`, `curate`, `use`, `export`; a shared `--db`/`-v`/`--dry-run` option set; helper `_open_db(db_path) -> Database`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from typer.testing import CliRunner
from bqpp.cli import app

runner = CliRunner()

def test_stats_runs_on_empty_db(tmp_path):
    result = runner.invoke(app, ["stats", "--db", str(tmp_path / "empty.sqlite")])
    assert result.exit_code == 0
    assert "0" in result.stdout
    assert "Total questions" in result.stdout

def test_all_spec_commands_are_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("harvest", "classify", "vet", "curate", "use", "stats", "export"):
        assert cmd in result.stdout

def test_dry_run_flag_exists_on_pipeline_commands():
    for cmd in ("harvest", "classify", "vet", "curate"):
        assert "--dry-run" in runner.invoke(app, [cmd, "--help"]).stdout
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_cli.py -v` — Expected: FAIL, `No module named 'bqpp.cli'`

- [ ] **Step 3: Implement `src/bqpp/cli.py` (skeleton)**

Register all seven commands now; Tasks 7–11 fill in the bodies. Each pipeline command takes `--dry-run`, `-v/--verbose`, `--force`, and `--db`. `stats` renders a `rich` table with total, by-format, by-vet-status and **per-subtopic coverage including zero-count subtopics** (so thin subtopics are visible — see the risk noted in Spec Amendments).

```python
"""Typer CLI. Contains no business logic — parses args and calls stage functions."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from bqpp.config import load_settings, load_taxonomy
from bqpp.db import Database

app = typer.Typer(add_completion=False, help="banco-questoes-pp — exam-question corpus & curation")
console = Console()

DbOpt = Annotated[Optional[Path], typer.Option("--db", help="Override the configured SQLite path")]
Verbose = Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging")]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Do everything except write")]
Force = Annotated[bool, typer.Option("--force", help="Redo work that is already done")]


def _open_db(db_path: Optional[Path]) -> Database:
    settings = load_settings()
    settings.ensure_dirs()
    db = Database.connect(db_path or settings.db_path)
    db.init_schema()
    return db


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")


@app.command()
def stats(db: DbOpt = None) -> None:
    """Corpus counts by subtopic / format / vet status."""
    database = _open_db(db)
    s = database.stats()
    taxonomy = load_taxonomy()

    console.print(f"[bold]Total questions:[/bold] {s['total']}")
    for title, data in (("By format", s["by_format"]),
                        ("By discipline", s["by_discipline"]),
                        ("By vet status", s["by_vet_status"]),
                        ("Sources", s["sources"])):
        table = Table(title=title, show_header=True)
        table.add_column("key"); table.add_column("count", justify="right")
        for k, v in sorted(data.items()):
            table.add_row(k, str(v))
        console.print(table)

    coverage = Table(title="Subtopic coverage", show_header=True)
    coverage.add_column("id"); coverage.add_column("label"); coverage.add_column("n", justify="right")
    for sid, label in taxonomy.labels.items():
        n = s["by_subtopic"].get(sid, 0)
        coverage.add_row(sid, label, f"[red]{n}[/red]" if n == 0 else str(n))
    console.print(coverage)
    database.close()


@app.command()
def harvest(source: Annotated[Optional[str], typer.Option("--source")] = None,
            db: DbOpt = None, dry_run: DryRun = False, force: Force = False,
            verbose: Verbose = False) -> None:
    """Download / refresh sources into the corpus."""
    _setup_logging(verbose)
    raise NotImplementedError("filled in by Task 7")


@app.command()
def classify(only_unclassified: Annotated[bool, typer.Option("--only-unclassified")] = True,
             limit: Annotated[Optional[int], typer.Option("--limit")] = None,
             db: DbOpt = None, dry_run: DryRun = False, force: Force = False,
             verbose: Verbose = False) -> None:
    """LLM classification against the taxonomy."""
    _setup_logging(verbose)
    raise NotImplementedError("filled in by Task 8")


@app.command()
def vet(only_unvetted: Annotated[bool, typer.Option("--only-unvetted")] = True,
        limit: Annotated[Optional[int], typer.Option("--limit")] = None,
        db: DbOpt = None, dry_run: DryRun = False, force: Force = False,
        verbose: Verbose = False) -> None:
    """Rule + LLM vetting."""
    _setup_logging(verbose)
    raise NotImplementedError("filled in by Task 9")


@app.command()
def curate(semester: Annotated[str, typer.Option("--semester")],
           subtopics: Annotated[Optional[str], typer.Option("--subtopics")] = None,
           db: DbOpt = None, dry_run: DryRun = False, verbose: Verbose = False) -> None:
    """Write per-subtopic markdown shortlists."""
    _setup_logging(verbose)
    raise NotImplementedError("filled in by Task 10")


@app.command()
def use(question_id: str,
        semester: Annotated[str, typer.Option("--semester")],
        subtopic: Annotated[str, typer.Option("--subtopic")],
        note: Annotated[Optional[str], typer.Option("--note")] = None,
        db: DbOpt = None) -> None:
    """Record the professor's pick in the usage log."""
    raise NotImplementedError("filled in by Task 10")


@app.command()
def export(db: DbOpt = None) -> None:
    """Write data/export/questions.jsonl."""
    raise NotImplementedError("filled in by Task 11")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v` — Expected: 3 passed

- [ ] **Step 5: Verify the M0 definition of done by hand**

Run: `uv run bqpp stats --db /tmp/empty.sqlite`
Expected: prints `Total questions: 0` and a subtopic-coverage table with all 20 rows at `0`.

- [ ] **Step 6: Commit**

```bash
git add src/bqpp/cli.py tests/test_cli.py
git commit -m "feat: typer CLI skeleton; bqpp stats runs on an empty DB (M0 done)"
```

---

## Task 7: HuggingFace harvest adapter (M1 ingestion)

**Files:**
- Create: `config/sources.yaml`, `src/bqpp/harvest/__init__.py`, `src/bqpp/harvest/registry.py`, `src/bqpp/harvest/hf_datasets.py`
- Modify: `src/bqpp/cli.py` (fill in `harvest`)
- Test: `tests/test_harvest_hf.py`, `tests/fixtures/oab_exams_sample.json`, `tests/fixtures/oab_bench_sample.json`

**Interfaces:**
- Consumes: `Database`, `SourceDocument`, `Question`, `source_doc_id`, `question_id`, `Settings`.
- Produces: `load_sources(path=None) -> list[SourceEntry]` where `SourceEntry` has `.id`, `.adapter`, `.params: dict`; `ingest_oab_exams(rows, *, source_id, doc, db, force) -> int`; `ingest_oab_bench(question_rows, guideline_rows, *, source_id, doc, db, force) -> int`; `harvest_source(entry, db, settings, *, dry_run, force) -> int`. Row inputs are plain `list[dict]`, so tests feed fixtures and production feeds parquet.

- [ ] **Step 1: Write the failing test** (fixtures are trimmed real rows — 3 of each — captured from the datasets-server)

```python
# tests/test_harvest_hf.py
import json
from pathlib import Path

import pytest
from bqpp.db import Database
from bqpp.harvest.hf_datasets import ingest_oab_bench, ingest_oab_exams
from bqpp.models import SourceDocument

FIX = Path(__file__).parent / "fixtures"

@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite"); d.init_schema()
    yield d
    d.close()

def _doc(sid="hf-oab-exams"):
    return SourceDocument(id="doc-" + sid, source_id=sid, url="https://huggingface.co/x",
                          fetched_at="2026-08-05T00:00:00Z", kind="dataset", banca="FGV",
                          carreira="oab")

def test_oab_exams_maps_columns_and_filters_by_question_type(db):
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    doc = _doc(); db.upsert_source_document(doc)
    n = ingest_oab_exams(rows, source_id="hf-oab-exams", doc=doc, db=db, force=False)
    assert n == 2                                  # 3 rows in, 1 is ETHICS and dropped
    q = next(db.iter_questions())
    assert q.format in ("mcq4", "mcq5")
    assert len(q.choices) == 4 and q.choices[0]["label"] == "A"
    assert q.answer_key in "ABCDE"
    assert q.stem and q.answer_rationale is None

def test_oab_exams_marks_nullified(db):
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    doc = _doc(); db.upsert_source_document(doc)
    ingest_oab_exams(rows, source_id="hf-oab-exams", doc=doc, db=db, force=False)
    assert any(q.nullified for q in db.iter_questions())

def test_oab_exams_ingest_is_idempotent(db):
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    doc = _doc(); db.upsert_source_document(doc)
    assert ingest_oab_exams(rows, source_id="hf-oab-exams", doc=doc, db=db, force=False) == 2
    assert ingest_oab_exams(rows, source_id="hf-oab-exams", doc=doc, db=db, force=False) == 0

def test_oab_bench_joins_guidelines_and_tags_peca(db):
    payload = json.loads((FIX / "oab_bench_sample.json").read_text(encoding="utf-8"))
    doc = _doc("hf-oab-bench"); db.upsert_source_document(doc)
    n = ingest_oab_bench(payload["questions"], payload["guidelines"],
                         source_id="hf-oab-bench", doc=doc, db=db, force=False)
    assert n == 2                                  # 1 penal questao + 1 penal peca; civil dropped
    by_fmt = {q.format: q for q in db.iter_questions()}
    assert set(by_fmt) == {"dissertativa", "peca"}
    assert by_fmt["dissertativa"].answer_rationale                 # guideline joined in
    assert "QUESTÃO" in by_fmt["dissertativa"].stem
    assert by_fmt["peca"].answer_key is None
```

- [ ] **Step 2: Capture the fixtures** (real rows, trimmed — run once, commit the output)

```bash
python3 - <<'PY'
import json, urllib.parse, urllib.request, pathlib
out = pathlib.Path("tests/fixtures"); out.mkdir(parents=True, exist_ok=True)

def rows(ds, cfg, n=30):
    u = ("https://datasets-server.huggingface.co/rows?dataset=" + urllib.parse.quote(ds, safe="")
         + f"&config={cfg}&split=train&offset=0&length={n}")
    return [r["row"] for r in json.load(urllib.request.urlopen(u, timeout=60))["rows"]]

ex = rows("eduagarcia/oab_exams", "default", 100)
sample = [r for r in ex if r["question_type"] == "CRIMINAL-PROCEDURE"][:1] \
       + [r for r in ex if r["question_type"] == "CRIMINAL"][:1] \
       + [r for r in ex if r["question_type"] == "ETHICS"][:1]
sample[1] = {**sample[1], "nullified": True}          # force one nullified row for the test
(out / "oab_exams_sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1))

qs = rows("maritaca-ai/oab-bench", "questions", 210)
gs = rows("maritaca-ai/oab-bench", "guidelines", 210)
pen = [q for q in qs if q["category"].endswith("direito_penal")]
keep = [next(q for q in pen if not q["question_id"].endswith("peca_profissional")),
        next(q for q in pen if q["question_id"].endswith("peca_profissional")),
        next(q for q in qs if q["category"].endswith("direito_civil"))]
ids = {q["question_id"] for q in keep}
(out / "oab_bench_sample.json").write_text(json.dumps(
    {"questions": keep, "guidelines": [g for g in gs if g["question_id"] in ids]},
    ensure_ascii=False, indent=1))
print("fixtures written")
PY
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `uv run pytest tests/test_harvest_hf.py -v` — Expected: FAIL, `No module named 'bqpp.harvest'`

- [ ] **Step 4: Create `config/sources.yaml`** (amended per A1/A2 — note the corrected filter values)

```yaml
sources:
  - id: hf-oab-exams
    adapter: hf_datasets
    dataset: eduagarcia/oab_exams
    config: default
    split: train
    # NB: the column is `question_type`; values are UPPER-HYPHEN codes, not prose.
    # CRIMINAL is kept alongside CRIMINAL-PROCEDURE — many CRIMINAL items turn on
    # procedural reasoning; the classify stage decides discipline, not this filter.
    question_type_filter: ["CRIMINAL-PROCEDURE", "CRIMINAL"]
    banca: FGV
    carreira: oab
    notes: bootstrap source — 2,210 rows total, 177 in the criminal slice, 2010–2018

  - id: hf-oab-bench
    adapter: hf_datasets
    dataset: maritaca-ai/oab-bench
    config: questions
    guidelines_config: guidelines
    split: train
    # NB: `category` is "{exam_number}_{discipline}", e.g. "39_direito_penal".
    category_suffix_filter: ["direito_penal"]
    banca: FGV
    carreira: oab
    notes: discursive bootstrap, 30 penal rows; official guidelines joined on question_id
```

- [ ] **Step 5: Implement `src/bqpp/harvest/registry.py`**

```python
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
```

- [ ] **Step 6: Implement `src/bqpp/harvest/hf_datasets.py`**

Key mapping decisions, all driven by the verified schemas:

- `oab_exams.choices` is a **struct of parallel lists** (`{"text": [...], "label": [...]}`) — zip them into the spec's `[{"label","text"}]` shape.
- `format` is `mcq4`/`mcq5` from `len(choices)`.
- `question_number` is `f"{exam_id}-{question_number}"` so ids stay unique across exams sharing a source document.
- `certame` is `f"OAB {exam_id}"`; `exam_year` is `int(exam_year)`.
- `oab-bench`: one row → one `dissertativa` whose `stem` is `statement` plus the numbered `turns`; a `_peca_profissional` row → `format="peca"`, stem only, no `answer_key`.
- Guidelines join: `choices[0]["turns"]` joined with sub-item headers into `answer_rationale`.

```python
"""HuggingFace dataset adapters (bootstrap ingestion, no PDF parsing)."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bqpp.db import Database
from bqpp.models import Question, SourceDocument, question_id, source_doc_id

log = logging.getLogger(__name__)


def _choices_from_struct(raw: Any) -> list[dict[str, str]] | None:
    """oab_exams stores choices as {'text': [...], 'label': [...]} (parallel lists)."""
    if not raw:
        return None
    if isinstance(raw, list):                       # already row-wise
        return [{"label": c["label"], "text": c["text"]} for c in raw]
    labels, texts = raw.get("label") or [], raw.get("text") or []
    return [{"label": l, "text": t} for l, t in zip(labels, texts)] or None


def ingest_oab_exams(rows: list[dict], *, source_id: str, doc: SourceDocument,
                     db: Database, force: bool = False,
                     question_type_filter: list[str] | None = None) -> int:
    allowed = set(question_type_filter or ["CRIMINAL-PROCEDURE", "CRIMINAL"])
    written = 0
    for row in rows:
        if row.get("question_type") not in allowed:
            continue
        choices = _choices_from_struct(row.get("choices"))
        if not choices:
            log.warning("skipping %s: no choices", row.get("id"))
            continue
        number = f"{row['exam_id']}-{row['question_number']}"
        q = Question(
            id=question_id(doc.id, number),
            source_doc_id=doc.id,
            question_number=number,
            format="mcq5" if len(choices) >= 5 else "mcq4",
            stem=row["question"],
            choices=choices,
            answer_key=(row.get("answerKey") or None),
            nullified=bool(row.get("nullified")),
        )
        if db.upsert_question(q, force=force):
            written += 1
    return written


def _stem_with_subitems(statement: str, turns: list[str]) -> str:
    parts = [statement.strip()]
    real = [t for t in (turns or []) if t and t.strip()]
    for i, t in enumerate(real, start=1):
        parts.append(f"\n**{chr(64 + i)})** {t.strip()}")
    return "\n".join(parts)


def _rationale_from_guideline(guideline: dict | None) -> str | None:
    if not guideline:
        return None
    choices = guideline.get("choices") or []
    if not choices:
        return None
    turns = [t for t in (choices[0].get("turns") or []) if t and t.strip()]
    if not turns:
        return None
    if len(turns) == 1:
        return turns[0].strip()
    return "\n\n".join(f"**{chr(64 + i)})** {t.strip()}" for i, t in enumerate(turns, start=1))


def ingest_oab_bench(question_rows: list[dict], guideline_rows: list[dict], *,
                     source_id: str, doc: SourceDocument, db: Database,
                     force: bool = False,
                     category_suffix_filter: list[str] | None = None) -> int:
    suffixes = tuple(category_suffix_filter or ["direito_penal"])
    guidelines = {g["question_id"]: g for g in guideline_rows
                  if g.get("model_id") == "guidelines"}
    written = 0
    for row in question_rows:
        if not row.get("category", "").endswith(suffixes):
            continue
        qid_raw = row["question_id"]
        is_peca = qid_raw.endswith("peca_profissional")
        q = Question(
            id=question_id(doc.id, qid_raw),
            source_doc_id=doc.id,
            question_number=qid_raw,
            format="peca" if is_peca else "dissertativa",
            stem=row["statement"].strip() if is_peca
                 else _stem_with_subitems(row["statement"], row.get("turns") or []),
            choices=None,
            answer_key=None,
            answer_rationale=_rationale_from_guideline(guidelines.get(qid_raw)),
        )
        if db.upsert_question(q, force=force):
            written += 1
    return written


def _download_parquet(dataset: str, config: str, split: str, raw_dir: Path) -> list[Path]:
    """Download the dataset's parquet shards into the on-disk cache; return local paths."""
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    files = [f for f in api.list_repo_files(dataset, repo_type="dataset")
             if f.endswith(".parquet") and f"/{config}/" in f and split in f]
    if not files:
        files = [f for f in api.list_repo_files(dataset, repo_type="dataset")
                 if f.endswith(".parquet")]
    target = raw_dir / dataset.replace("/", "__")
    target.mkdir(parents=True, exist_ok=True)
    return [Path(hf_hub_download(dataset, f, repo_type="dataset", local_dir=target))
            for f in files]


def _read_rows(paths: list[Path]) -> list[dict]:
    import pyarrow.parquet as pq

    rows: list[dict] = []
    for p in paths:
        rows.extend(pq.read_table(p).to_pylist())
    return rows


def harvest_source(entry, db: Database, settings, *, dry_run: bool = False,
                   force: bool = False) -> int:
    """Download, register provenance, and ingest one hf_datasets source entry."""
    p = entry.params
    dataset, config, split = p["dataset"], p.get("config", "default"), p.get("split", "train")
    paths = _download_parquet(dataset, config, split, settings.raw_dir)
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    doc = SourceDocument(
        id=source_doc_id(digest.digest()),
        source_id=entry.id,
        url=f"https://huggingface.co/datasets/{dataset}",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        kind="dataset",
        banca=p.get("banca"),
        carreira=p.get("carreira"),
        certame=p.get("certame"),
        local_path=str(paths[0].parent) if paths else None,
    )
    rows = _read_rows(paths)
    if dry_run:
        log.info("[dry-run] %s: %d rows, doc id %s", entry.id, len(rows), doc.id[:12])
        return 0

    db.upsert_source_document(doc, force=force)
    if "guidelines_config" in p:
        g_paths = _download_parquet(dataset, p["guidelines_config"], split, settings.raw_dir)
        return ingest_oab_bench(rows, _read_rows(g_paths), source_id=entry.id, doc=doc, db=db,
                                force=force,
                                category_suffix_filter=p.get("category_suffix_filter"))
    return ingest_oab_exams(rows, source_id=entry.id, doc=doc, db=db, force=force,
                            question_type_filter=p.get("question_type_filter"))
```

- [ ] **Step 7: Wire the `harvest` CLI command**

```python
@app.command()
def harvest(source: Annotated[Optional[str], typer.Option("--source")] = None,
            db: DbOpt = None, dry_run: DryRun = False, force: Force = False,
            verbose: Verbose = False) -> None:
    """Download / refresh sources into the corpus."""
    _setup_logging(verbose)
    from bqpp.harvest.hf_datasets import harvest_source
    from bqpp.harvest.registry import load_sources

    settings = load_settings(); settings.ensure_dirs()
    database = _open_db(db)
    total = 0
    for entry in load_sources():
        if source and entry.id != source:
            continue
        if entry.adapter != "hf_datasets":
            console.print(f"[yellow]skipping {entry.id}: adapter "
                          f"{entry.adapter!r} lands in M2/M3[/yellow]")
            continue
        n = harvest_source(entry, database, settings, dry_run=dry_run, force=force)
        console.print(f"{entry.id}: {n} new questions")
        total += n
    console.print(f"[bold]{total}[/bold] new questions harvested")
    database.close()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_harvest_hf.py -v` — Expected: 4 passed

- [ ] **Step 9: Run the real harvest and sanity-check the counts**

Run: `uv run bqpp harvest -v && uv run bqpp stats`
Expected: ~177 questions from `hf-oab-exams` and ~30 from `hf-oab-bench` (≈207 total); formats include `mcq4`/`mcq5`/`dissertativa`/`peca`; every subtopic still `0` (classification has not run).

- [ ] **Step 10: Commit**

```bash
git add config/sources.yaml src/bqpp/harvest src/bqpp/cli.py tests/test_harvest_hf.py tests/fixtures
git commit -m "feat: HuggingFace harvest adapter for oab_exams and oab-bench"
```

---

## Task 8: Classification stage

**Files:**
- Create: `prompts/classify.md`, `src/bqpp/classify.py`
- Modify: `src/bqpp/cli.py` (fill in `classify`)
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `LLMClient.complete_json`, `Taxonomy`, `Database`.
- Produces: `CLASSIFY_SCHEMA: dict`; `build_prompt(question, taxonomy) -> tuple[str, str]` returning `(system, user)`; `classify_question(q, *, client, taxonomy) -> dict` returning the validated + taxonomy-checked fields; `run_classify(db, client, taxonomy, *, only_unclassified=True, limit=None, dry_run=False, force=False) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify.py
import json
import pytest
from bqpp.classify import CLASSIFY_SCHEMA, classify_question, run_classify
from bqpp.config import load_taxonomy
from bqpp.db import Database
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import Question, SourceDocument

@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite"); d.init_schema()
    d.upsert_source_document(SourceDocument(id="d", source_id="s", url="u",
                                            fetched_at="t", kind="dataset"))
    d.upsert_question(Question(id="q1", source_doc_id="d", question_number="1",
                               format="mcq4", stem="Sobre prisão preventiva...",
                               choices=[{"label": "A", "text": "a"}], answer_key="A"))
    yield d
    d.close()

def _payload(**over):
    base = {"discipline": "direito-processual-penal", "subtopic_ids": ["T1.2"],
            "difficulty": "medium", "note": ""}
    return json.dumps({**base, **over}, ensure_ascii=False)

def test_schema_is_openai_strict_compatible():
    assert CLASSIFY_SCHEMA["additionalProperties"] is False
    assert set(CLASSIFY_SCHEMA["required"]) == set(CLASSIFY_SCHEMA["properties"])

def test_happy_path_writes_classification(db):
    client = LLMClient(FakeBackend([_payload()]))
    assert run_classify(db, client, load_taxonomy()) == 1
    q = db.get_question("q1")
    assert q.subtopic_ids == ["T1.2"] and q.discipline == "direito-processual-penal"
    assert q.classified_at and q.classify_model

def test_prompt_carries_taxonomy_and_verbatim_question(db):
    be = FakeBackend([_payload()])
    run_classify(db, LLMClient(be), load_taxonomy())
    user = be.calls[0]["user"]
    assert "T1.2" in user and "Prisão temporária" in user
    assert "Sobre prisão preventiva" in user

def test_invalid_subtopic_id_retries_once_then_stores_unclassified(db):
    # spec §9.3: an invalid subtopic id ⇒ retry once with the error, then store as unclassified
    be = FakeBackend([_payload(subtopic_ids=["T9.9"]), _payload(subtopic_ids=["T9.9"])])
    run_classify(db, LLMClient(be), load_taxonomy())
    q = db.get_question("q1")
    assert q.subtopic_ids == []
    assert "T9.9" in (q.classified_note or "")
    assert len(be.calls) == 2

def test_valid_retry_is_accepted(db):
    be = FakeBackend([_payload(subtopic_ids=["T9.9"]), _payload(subtopic_ids=["T2.4"])])
    run_classify(db, LLMClient(be), load_taxonomy())
    assert db.get_question("q1").subtopic_ids == ["T2.4"]

def test_only_unclassified_skips_done_work(db):
    run_classify(db, LLMClient(FakeBackend([_payload()])), load_taxonomy())
    be = FakeBackend([])
    assert run_classify(db, LLMClient(be), load_taxonomy()) == 0
    assert be.calls == []

def test_dry_run_writes_nothing(db):
    be = FakeBackend([_payload()])
    run_classify(db, LLMClient(be), load_taxonomy(), dry_run=True)
    assert db.get_question("q1").classified_at is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_classify.py -v` — Expected: FAIL, `No module named 'bqpp.classify'`

- [ ] **Step 3: Create `prompts/classify.md`** — the spec §9.3 contract kept **exactly**, with `{taxonomy_yaml}` and `{question_json}` placeholders:

```markdown
You are classifying one question from a Brazilian legal exam. The question is in Portuguese.

1. Decide the discipline: `direito-processual-penal`, `other`, or `mixed` (procedural-penal reasoning is required but combined with another discipline).
2. If processual penal (or mixed), assign 1–2 subtopic ids from the taxonomy below (primary first). If none fits, return an empty list and explain briefly in `note`.
3. Estimate `difficulty` (easy/medium/hard) for a final-year law student.

Return ONLY JSON matching the schema. Do not translate or alter the question.

## Taxonomy

```yaml
{taxonomy_yaml}
```

## Question

```json
{question_json}
```
```

- [ ] **Step 4: Implement `src/bqpp/classify.py`**

```python
"""LLM classification stage (spec §9.3). Tier: fast."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bqpp.config import PROJECT_ROOT, Taxonomy
from bqpp.db import Database
from bqpp.llm.base import LLMError
from bqpp.llm.client import LLMClient
from bqpp.models import Question

log = logging.getLogger(__name__)
PROMPT_PATH = PROJECT_ROOT / "prompts" / "classify.md"

# additionalProperties:false + all-keys-required keeps this valid for OpenAI strict mode;
# to_gemini_schema() strips what Gemini rejects.
CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "discipline": {"type": "string",
                       "enum": ["direito-processual-penal", "other", "mixed"]},
        "subtopic_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
        "note": {"type": "string"},
    },
    "required": ["discipline", "subtopic_ids", "difficulty", "note"],
    "additionalProperties": False,
}

SYSTEM = ("You are a careful Brazilian legal-education assistant. You classify exam questions "
          "against a fixed taxonomy and return strict JSON. Never translate or alter the question.")


def build_prompt(question: Question, taxonomy: Taxonomy) -> tuple[str, str]:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    user = template.replace("{taxonomy_yaml}", taxonomy.as_prompt_yaml()).replace(
        "{question_json}", json.dumps(question.to_prompt_payload(), ensure_ascii=False, indent=1))
    return SYSTEM, user


def classify_question(question: Question, *, client: LLMClient, taxonomy: Taxonomy) -> dict:
    """Classify one question; retry once on an invalid subtopic id, then fall back to unclassified."""
    system, user = build_prompt(question, taxonomy)
    result = client.complete_json(system=system, user=user, json_schema=CLASSIFY_SCHEMA,
                                  model_tier="fast", stage="classify")
    bad = taxonomy.validate_ids(result["subtopic_ids"])
    if bad:
        retry_user = (f"{user}\n\n---\nYour previous answer used subtopic ids that do not exist: "
                      f"{bad}. Use only ids from the taxonomy above, or return an empty list.")
        result = client.complete_json(system=system, user=retry_user,
                                      json_schema=CLASSIFY_SCHEMA, model_tier="fast",
                                      stage="classify")
        bad = taxonomy.validate_ids(result["subtopic_ids"])
        if bad:
            log.warning("question %s: model insisted on invalid ids %s; storing unclassified",
                        question.id, bad)
            note = (result.get("note") or "").strip()
            result = {**result, "subtopic_ids": [],
                      "note": f"invalid subtopic ids returned by model: {bad}. {note}".strip()}
    return result


def run_classify(db: Database, client: LLMClient, taxonomy: Taxonomy, *,
                 only_unclassified: bool = True, limit: int | None = None,
                 dry_run: bool = False, force: bool = False) -> int:
    questions = list(db.iter_questions(unclassified=only_unclassified and not force))
    if limit:
        questions = questions[:limit]
    done = 0
    for q in questions:
        try:
            result = classify_question(q, client=client, taxonomy=taxonomy)
        except LLMError as exc:
            log.error("classification failed for %s: %s", q.id, exc)
            continue
        if dry_run:
            log.info("[dry-run] %s -> %s %s", q.id[:12], result["discipline"],
                     result["subtopic_ids"])
            done += 1
            continue
        db.update_classification(
            q.id, discipline=result["discipline"], subtopic_ids=result["subtopic_ids"],
            difficulty=result["difficulty"], classified_note=result.get("note") or None,
            classify_model=client.backend.name,
            classified_at=datetime.now(timezone.utc).isoformat(),
        )
        done += 1
    return done
```

- [ ] **Step 5: Wire the `classify` CLI command**

```python
@app.command()
def classify(only_unclassified: Annotated[bool, typer.Option("--only-unclassified")] = True,
             limit: Annotated[Optional[int], typer.Option("--limit")] = None,
             db: DbOpt = None, dry_run: DryRun = False, force: Force = False,
             verbose: Verbose = False) -> None:
    """LLM classification against the taxonomy."""
    _setup_logging(verbose)
    from bqpp.classify import run_classify
    from bqpp.llm.factory import build_client

    database = _open_db(db)
    client = build_client(load_settings(), db=database)
    n = run_classify(database, client, load_taxonomy(), only_unclassified=only_unclassified,
                     limit=limit, dry_run=dry_run, force=force)
    console.print(f"classified [bold]{n}[/bold] questions")
    database.close()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_classify.py -v` — Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add prompts/classify.md src/bqpp/classify.py src/bqpp/cli.py tests/test_classify.py
git commit -m "feat: LLM classification stage with taxonomy-id validation"
```

---

## Task 9: Vetting stage (rules + LLM)

**Files:**
- Create: `config/law_watchlist.yaml`, `prompts/vet.md`, `src/bqpp/vet.py`
- Modify: `src/bqpp/cli.py` (fill in `vet`)
- Test: `tests/test_vet.py`

**Interfaces:**
- Consumes: `LLMClient`, `Database`, `Taxonomy`.
- Produces: `VET_SCHEMA: dict`; `load_watchlist(path=None) -> list[WatchlistEntry]` (`.id`, `.change`, `.effective`, `.affects`); `apply_rules(q, watchlist) -> tuple[VetStatus | None, list[VetReason], list[WatchlistEntry]]`; `vet_question(q, *, client, watchlist) -> dict`; `run_vet(db, client, watchlist, *, only_unvetted=True, limit=None, dry_run=False, force=False) -> int`.

Rule layer per spec §10.1, in this precedence order:
1. `nullified` → `rejected`, reason `anulada`. **Short-circuits — no LLM call.**
2. Missing `answer_key` on `mcq*`/`certo_errado` → `flagged`, reason `no_gabarito`. Does *not* short-circuit.
3. `exam_year < watchlist.effective.year` for any watchlist entry whose `affects` intersects the question's `subtopic_ids` → that entry is injected into the LLM prompt.

Verdict mapping per spec §10.2: `resposta_mudou_mas_util` maps to **`flagged`, not `rejected`**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vet.py
import json
import pytest
from bqpp.db import Database
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import Question, SourceDocument
from bqpp.vet import VET_SCHEMA, apply_rules, load_watchlist, run_vet

@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite"); d.init_schema()
    d.upsert_source_document(SourceDocument(id="d", source_id="s", url="u", fetched_at="t",
                                            kind="dataset", exam_year=2015))
    yield d
    d.close()

def _q(qid="q1", **over):
    base = dict(id=qid, source_doc_id="d", question_number="1", format="mcq4",
                stem="s", choices=[{"label": "A", "text": "a"}], answer_key="A",
                discipline="direito-processual-penal", subtopic_ids=["T1.2"])
    return Question(**{**base, **over})

def _verdict(**over):
    base = {"verdict": "ok", "reasons": [], "pedagogy_note": "boa questão"}
    return json.dumps({**base, **over}, ensure_ascii=False)

def test_schema_is_openai_strict_compatible():
    assert VET_SCHEMA["additionalProperties"] is False
    assert set(VET_SCHEMA["required"]) == set(VET_SCHEMA["properties"])

def test_nullified_is_rejected_without_an_llm_call(db):
    db.upsert_question(_q(nullified=True))
    be = FakeBackend([])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "rejected"
    assert [r.code for r in q.vet_reasons] == ["anulada"]
    assert be.calls == []

def test_missing_gabarito_is_flagged_and_still_vetted_by_llm(db):
    db.upsert_question(_q(answer_key=None))
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "flagged"
    assert "no_gabarito" in [r.code for r in q.vet_reasons]
    assert len(be.calls) == 1

def test_watchlist_entry_is_injected_for_affected_old_questions(db):
    db.upsert_question(_q())          # exam_year 2015, subtopic T1.2 -> pacote-anticrime
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    assert "13.964" in be.calls[0]["user"] or "Anticrime" in be.calls[0]["user"]

def test_unaffected_subtopic_gets_no_watchlist_injection(db):
    db.upsert_question(_q(subtopic_ids=["T2.8"]))     # júri — not in any v1 watchlist entry
    be = FakeBackend([_verdict()])
    run_vet(db, LLMClient(be), load_watchlist())
    assert "Anticrime" not in be.calls[0]["user"]

def test_resposta_mudou_mas_util_maps_to_flagged_not_rejected(db):
    db.upsert_question(_q())
    be = FakeBackend([_verdict(verdict="rejected",
                               reasons=[{"code": "resposta_mudou_mas_util",
                                         "detail": "art. 316 CPP mudou"}])])
    run_vet(db, LLMClient(be), load_watchlist())
    q = db.get_question("q1")
    assert q.vet_status == "flagged"
    assert q.pedagogy_note

def test_only_unvetted_skips_done_work(db):
    db.upsert_question(_q())
    run_vet(db, LLMClient(FakeBackend([_verdict()])), load_watchlist())
    be = FakeBackend([])
    assert run_vet(db, LLMClient(be), load_watchlist()) == 0

def test_apply_rules_is_pure():
    status, reasons, entries = apply_rules(_q(nullified=True), load_watchlist())
    assert status == "rejected" and reasons[0].code == "anulada" and entries == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_vet.py -v` — Expected: FAIL, `No module named 'bqpp.vet'`

- [ ] **Step 3: Create `config/law_watchlist.yaml`** — the spec §10.1 v1 seed, verbatim (three entries: `pacote-anticrime`, `execucao-provisoria-pena`, `juiz-garantias-vigencia`), with a header comment noting the professor maintains this file.

- [ ] **Step 4: Create `prompts/vet.md`** — the spec §10.2 contract kept exactly, with `{current_year}`, `{question_json}` and `{watchlist_block}` placeholders:

```markdown
You are vetting a Brazilian criminal-procedure exam question for classroom use in {current_year}. Assess:

1. **Outdatedness:** given legislative and jurisprudential changes (including, but not limited to, the listed watchlist items), is the official answer still correct today? If the *question* is still pedagogically usable but the *answer* changed, say so — that can be classroom gold, not garbage.
2. **Quality:** is the question well-formed, unambiguous, and does it actually test use of concepts (not pure memorization of article numbers)?

Return JSON: `{verdict: ok|flagged|rejected, reasons: [{code, detail}], pedagogy_note}`.
Codes: `desatualizada`, `resposta_mudou_mas_util`, `ambigua`, `decoreba`, `outros`.

{watchlist_block}

## Question

```json
{question_json}
```
```

- [ ] **Step 5: Implement `src/bqpp/vet.py`**

```python
"""Vetting stage: cheap rules first, then the strong-tier LLM (spec §10)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel

from bqpp.config import CONFIG_DIR, PROJECT_ROOT
from bqpp.db import Database
from bqpp.llm.base import LLMError
from bqpp.llm.client import LLMClient
from bqpp.models import Question, VetReason, VetStatus

log = logging.getLogger(__name__)
PROMPT_PATH = PROJECT_ROOT / "prompts" / "vet.md"

VET_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "flagged", "rejected"]},
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "enum": ["desatualizada", "resposta_mudou_mas_util",
                                      "ambigua", "decoreba", "outros"]},
                    "detail": {"type": "string"},
                },
                "required": ["code", "detail"],
                "additionalProperties": False,
            },
        },
        "pedagogy_note": {"type": "string"},
    },
    "required": ["verdict", "reasons", "pedagogy_note"],
    "additionalProperties": False,
}

SYSTEM = ("You are a Brazilian criminal-procedure professor vetting exam questions for classroom "
          "use. Be precise about what changed in the law and why it matters. Return strict JSON.")

# A question whose answer changed is teaching material, not garbage (spec §10.2).
_SOFTENING_CODE = "resposta_mudou_mas_util"


class WatchlistEntry(BaseModel):
    id: str
    change: str
    effective: date
    affects: list[str]


def load_watchlist(path: Path | None = None) -> list[WatchlistEntry]:
    path = path or CONFIG_DIR / "law_watchlist.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [WatchlistEntry(**e) for e in raw["watchlist"]]


def apply_rules(question: Question, watchlist: list[WatchlistEntry], *,
                exam_year: int | None = None
                ) -> tuple[VetStatus | None, list[VetReason], list[WatchlistEntry]]:
    """Return (terminal_status_or_None, reasons, watchlist entries to inject)."""
    if question.nullified:
        return "rejected", [VetReason(code="anulada",
                                      detail="questão anulada pela banca")], []

    reasons: list[VetReason] = []
    if question.format in ("mcq4", "mcq5", "certo_errado") and not question.answer_key:
        reasons.append(VetReason(code="no_gabarito",
                                 detail="sem gabarito alinhado para questão objetiva"))

    matched: list[WatchlistEntry] = []
    if exam_year is not None:
        subs = set(question.subtopic_ids)
        matched = [e for e in watchlist
                   if exam_year < e.effective.year and subs & set(e.affects)]
    return None, reasons, matched


def build_prompt(question: Question, entries: list[WatchlistEntry]) -> tuple[str, str]:
    if entries:
        block = "## Watchlist (mandatory considerations)\n\n" + "\n".join(
            f"- **{e.id}** (vigente desde {e.effective.isoformat()}): {e.change.strip()}"
            for e in entries)
    else:
        block = ""
    user = (PROMPT_PATH.read_text(encoding="utf-8")
            .replace("{current_year}", str(date.today().year))
            .replace("{watchlist_block}", block)
            .replace("{question_json}",
                     json.dumps(question.to_prompt_payload(), ensure_ascii=False, indent=1)))
    return SYSTEM, user


def vet_question(question: Question, *, client: LLMClient,
                 entries: list[WatchlistEntry]) -> dict:
    system, user = build_prompt(question, entries)
    return client.complete_json(system=system, user=user, json_schema=VET_SCHEMA,
                                model_tier="strong", stage="vet")


def _merge_verdict(rule_reasons: list[VetReason], result: dict) -> tuple[VetStatus, list[VetReason]]:
    reasons = list(rule_reasons) + [VetReason(**r) for r in result.get("reasons", [])]
    verdict: VetStatus = result["verdict"]
    if verdict == "rejected" and any(r.code == _SOFTENING_CODE for r in reasons):
        verdict = "flagged"
    if verdict == "ok" and any(r.code == "no_gabarito" for r in rule_reasons):
        verdict = "flagged"
    return verdict, reasons


def run_vet(db: Database, client: LLMClient, watchlist: list[WatchlistEntry], *,
            only_unvetted: bool = True, limit: int | None = None,
            dry_run: bool = False, force: bool = False) -> int:
    questions = list(db.iter_questions(unvetted=only_unvetted and not force))
    if limit:
        questions = questions[:limit]
    done = 0
    for q in questions:
        doc = db.get_source_document(q.source_doc_id)
        terminal, rule_reasons, entries = apply_rules(q, watchlist,
                                                      exam_year=doc.exam_year if doc else None)
        if terminal is not None:
            status, reasons, note = terminal, rule_reasons, None
        else:
            try:
                result = vet_question(q, client=client, entries=entries)
            except LLMError as exc:
                log.error("vetting failed for %s: %s", q.id, exc)
                continue
            status, reasons = _merge_verdict(rule_reasons, result)
            note = result.get("pedagogy_note") or None

        if dry_run:
            log.info("[dry-run] %s -> %s %s", q.id[:12], status, [r.code for r in reasons])
            done += 1
            continue
        db.update_vetting(q.id, vet_status=status, vet_reasons=reasons, pedagogy_note=note,
                          vet_model=client.backend.name,
                          vetted_at=datetime.now(timezone.utc).isoformat())
        done += 1
    return done
```

- [ ] **Step 6: Wire the `vet` CLI command** (same shape as `classify`: build the client, call `run_vet`, print the count).

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_vet.py -v` — Expected: 8 passed

- [ ] **Step 8: Commit**

```bash
git add config/law_watchlist.yaml prompts/vet.md src/bqpp/vet.py src/bqpp/cli.py tests/test_vet.py
git commit -m "feat: two-layer vetting stage with law watchlist injection"
```

---

## Task 10: Curation stage — shortlists and the usage log

**Files:**
- Create: `src/bqpp/curate.py`
- Modify: `src/bqpp/cli.py` (fill in `curate` and `use`)
- Test: `tests/test_curate.py`

**Interfaces:**
- Consumes: `Database`, `Taxonomy`, `Settings.ranking`.
- Produces: `score(question, doc, ranking) -> float`; `rank_candidates(candidates, ranking) -> list[tuple[Question, SourceDocument]]`; `render_shortlist(subtopic_id, label, ranked, *, semester) -> str`; `run_curate(db, taxonomy, settings, *, semester, subtopic_ids=None, dry_run=False) -> dict[str, int]`; `record_use(db, question_id, semester, subtopic, note=None)`.

Ranking (spec §11.2, deterministic, **no LLM**): prefer `ok` over `flagged`; prefer `dissertativa > certo_errado > mcq`; prefer newer `exam_year`; prefer questions with `answer_rationale`; tie-break by carreira diversity across the semester's shortlists.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_curate.py
import pytest
from bqpp.config import load_settings, load_taxonomy
from bqpp.curate import record_use, render_shortlist, run_curate, score
from bqpp.db import Database
from bqpp.models import Question, SourceDocument, VetReason

@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite"); d.init_schema()
    d.upsert_source_document(SourceDocument(id="d18", source_id="hf-oab-exams", url="u",
                                            fetched_at="t", kind="dataset", banca="FGV",
                                            carreira="oab", certame="OAB 2018-01", exam_year=2018))
    d.upsert_source_document(SourceDocument(id="d10", source_id="hf-oab-exams", url="u",
                                            fetched_at="t", kind="dataset", banca="FGV",
                                            carreira="oab", certame="OAB 2010-01", exam_year=2010))
    yield d
    d.close()

def _q(qid, doc="d18", fmt="mcq4", status="ok", rationale=None):
    return Question(id=qid, source_doc_id=doc, question_number=qid, format=fmt,
                    stem=f"Enunciado {qid}", choices=[{"label": "A", "text": "a"}],
                    answer_key="A", answer_rationale=rationale,
                    discipline="direito-processual-penal", subtopic_ids=["T1.2"],
                    vet_status=status, pedagogy_note="nota")

def test_ranking_prefers_ok_open_format_recent_and_rationale(db):
    r = load_settings().ranking
    d18 = db.get_source_document("d18"); d10 = db.get_source_document("d10")
    assert score(_q("a", fmt="dissertativa"), d18, r) > score(_q("b", fmt="mcq4"), d18, r)
    assert score(_q("c", status="ok"), d18, r) > score(_q("d", status="flagged"), d18, r)
    assert score(_q("e", doc="d18"), d18, r) > score(_q("f", doc="d10"), d10, r)
    assert score(_q("g", rationale="gabarito"), d18, r) > score(_q("h"), d18, r)

def test_rejected_and_previously_used_are_excluded(db):
    db.upsert_question(_q("keep"))
    db.upsert_question(_q("bad", status="rejected"))
    db.upsert_question(_q("used"))
    record_use(db, "used", "2025.2", "T1.2")
    written = run_curate(db, load_taxonomy(), load_settings(),
                         semester="2026.2", subtopic_ids=["T1.2"])
    path = load_settings().shortlist_dir / "2026-2" / "T1.2.md"
    text = path.read_text(encoding="utf-8")
    assert "keep" in text and "bad" not in text and "used" not in text
    assert written["T1.2"] == 1

def test_shortlist_is_self_contained_markdown(db):
    q = _q("q1", fmt="dissertativa", rationale="Gabarito comentado da banca")
    q.vet_status = "flagged"
    q.vet_reasons = [VetReason(code="resposta_mudou_mas_util", detail="art. 316 CPP")]
    doc = db.get_source_document("d18")
    md = render_shortlist("T1.2", "Prisão preventiva", [(q, doc)], semester="2026.2")
    assert "Enunciado q1" in md                      # verbatim stem
    assert "<details>" in md and "Gabarito comentado da banca" in md
    assert "FGV" in md and "OAB 2018-01" in md and "2018" in md
    assert "resposta_mudou_mas_util" in md
    assert "bqpp use q1 --semester 2026.2 --subtopic T1.2" in md
    assert "⚠" in md                                 # flagged banner

def test_empty_subtopic_still_writes_a_file_saying_so(db):
    run_curate(db, load_taxonomy(), load_settings(), semester="2026.2", subtopic_ids=["T3.3"])
    text = (load_settings().shortlist_dir / "2026-2" / "T3.3.md").read_text(encoding="utf-8")
    assert "Nenhum candidato" in text
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_curate.py -v` — Expected: FAIL, `No module named 'bqpp.curate'`

- [ ] **Step 3: Implement `src/bqpp/curate.py`**

```python
"""Curation stage: deterministic ranking + self-contained markdown shortlists (spec §11)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bqpp.config import RankingSettings, Settings, Taxonomy
from bqpp.db import Database
from bqpp.models import Question, SourceDocument, UsageEntry

log = logging.getLogger(__name__)


def score(question: Question, doc: SourceDocument | None, ranking: RankingSettings) -> float:
    s = ranking.format_weights.get(question.format, 0.0)
    if question.vet_status == "ok":
        s += ranking.vet_ok_bonus
    if question.answer_rationale:
        s += ranking.rationale_bonus
    if doc and doc.exam_year:
        s += (doc.exam_year - 2000) * ranking.year_weight
    return s


def rank_candidates(candidates: list[tuple[Question, SourceDocument | None]],
                    ranking: RankingSettings,
                    seen_carreiras: set[str] | None = None
                    ) -> list[tuple[Question, SourceDocument | None]]:
    seen = seen_carreiras or set()

    def key(item: tuple[Question, SourceDocument | None]) -> tuple:
        q, doc = item
        carreira = (doc.carreira if doc else None) or "outra"
        diversity = 1.0 if carreira not in seen else 0.0   # tie-break: carreira diversity
        return (-(score(q, doc, ranking) + diversity), q.id)

    return sorted(candidates, key=key)


def _fmt_choices(q: Question) -> str:
    if not q.choices:
        return ""
    return "\n".join(f"- **{c['label']})** {c['text']}" for c in q.choices)


def render_shortlist(subtopic_id: str, label: str,
                     ranked: list[tuple[Question, SourceDocument | None]], *,
                     semester: str) -> str:
    out = [f"# {subtopic_id} — {label}", "",
           f"Semestre **{semester}** · gerado em {datetime.now(timezone.utc).date().isoformat()}",
           "", "> Escolha uma questão e registre com o comando indicado ao final de cada entrada.",
           ""]
    if not ranked:
        out += ["## Nenhum candidato",
                "", "Nenhuma questão vetada foi classificada neste subtópico. "
                "Amplie o corpus (M2/M3) ou revise a taxonomia.", ""]
        return "\n".join(out)

    for i, (q, doc) in enumerate(ranked, start=1):
        prov = " · ".join(filter(None, [
            doc.banca if doc else None, doc.certame if doc else None,
            str(doc.exam_year) if doc and doc.exam_year else None,
            (doc.carreira or "").upper() if doc and doc.carreira else None]))
        out += [f"## {i}. {q.format} — {q.vet_status}", ""]
        if q.vet_status == "flagged":
            codes = ", ".join(f"`{r.code}`" for r in q.vet_reasons) or "—"
            out += [f"> ⚠ **Sinalizada:** {codes}", ""]
            for r in q.vet_reasons:
                out.append(f"> - **{r.code}**: {r.detail}")
            out.append("")
        out += [q.stem, ""]
        if q.choices:
            out += [_fmt_choices(q), ""]
        out += ["<details>", "<summary><strong>Gabarito e fundamentação</strong></summary>", ""]
        out.append(f"**Resposta:** {q.answer_key or '— (discursiva)'}")
        if q.answer_rationale:
            out += ["", "**Gabarito comentado da banca:**", "", q.answer_rationale]
        if q.pedagogy_note:
            out += ["", f"**Nota pedagógica (LLM):** {q.pedagogy_note}"]
        out += ["", "</details>", ""]
        out += [f"*Fonte: {prov or 'desconhecida'} · {doc.url if doc else ''}*", "",
                "```bash", f"bqpp use {q.id} --semester {semester} --subtopic {subtopic_id}",
                "```", "", "---", ""]
    return "\n".join(out)


def run_curate(db: Database, taxonomy: Taxonomy, settings: Settings, *, semester: str,
               subtopic_ids: list[str] | None = None, dry_run: bool = False) -> dict[str, int]:
    targets = subtopic_ids or list(taxonomy.labels)
    used = db.used_question_ids()
    out_dir = settings.shortlist_dir / semester.replace(".", "-")
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    seen_carreiras: set[str] = set()
    written: dict[str, int] = {}
    for sid in targets:
        candidates = []
        for q in db.iter_questions(subtopic=sid):
            if q.vet_status not in ("ok", "flagged") or q.id in used:
                continue
            candidates.append((q, db.get_source_document(q.source_doc_id)))
        ranked = rank_candidates(candidates, settings.ranking, seen_carreiras)
        top = ranked[: settings.ranking.shortlist_size]
        for _, doc in top:
            if doc and doc.carreira:
                seen_carreiras.add(doc.carreira)
        written[sid] = len(top)
        md = render_shortlist(sid, taxonomy.labels[sid], top, semester=semester)
        if dry_run:
            log.info("[dry-run] %s: %d candidates", sid, len(top))
            continue
        (out_dir / f"{sid}.md").write_text(md, encoding="utf-8")
    return written


def record_use(db: Database, question_id: str, semester: str, subtopic: str,
               note: str | None = None) -> None:
    db.record_usage(UsageEntry(question_id=question_id, semester=semester, subtopic_id=subtopic,
                               used_at=datetime.now(timezone.utc).isoformat(), note=note))
```

- [ ] **Step 4: Wire the `curate` and `use` CLI commands**

```python
@app.command()
def curate(semester: Annotated[str, typer.Option("--semester")],
           subtopics: Annotated[Optional[str], typer.Option("--subtopics")] = None,
           db: DbOpt = None, dry_run: DryRun = False, verbose: Verbose = False) -> None:
    """Write per-subtopic markdown shortlists."""
    _setup_logging(verbose)
    from bqpp.curate import run_curate

    settings = load_settings(); settings.ensure_dirs()
    database = _open_db(db)
    ids = [s.strip() for s in subtopics.split(",")] if subtopics else None
    written = run_curate(database, load_taxonomy(), settings, semester=semester,
                         subtopic_ids=ids, dry_run=dry_run)
    empty = [k for k, v in written.items() if v == 0]
    console.print(f"wrote {len(written)} shortlists to "
                  f"{settings.shortlist_dir / semester.replace('.', '-')}")
    if empty:
        console.print(f"[yellow]{len(empty)} subtopics with no candidates: "
                      f"{', '.join(empty)}[/yellow]")
    database.close()


@app.command()
def use(question_id: str,
        semester: Annotated[str, typer.Option("--semester")],
        subtopic: Annotated[str, typer.Option("--subtopic")],
        note: Annotated[Optional[str], typer.Option("--note")] = None,
        db: DbOpt = None) -> None:
    """Record the professor's pick in the usage log."""
    from bqpp.curate import record_use

    database = _open_db(db)
    if database.get_question(question_id) is None:
        console.print(f"[red]no such question: {question_id}[/red]")
        raise typer.Exit(code=1)
    record_use(database, question_id, semester, subtopic, note)
    console.print(f"recorded {question_id[:12]}… for {semester} / {subtopic}")
    database.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_curate.py -v` — Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/bqpp/curate.py src/bqpp/cli.py tests/test_curate.py
git commit -m "feat: curation stage with deterministic ranking and markdown shortlists"
```

---

## Task 11: JSONL export, README, and the end-to-end smoke test

**Files:**
- Create: `src/bqpp/export.py`, `README.md`
- Modify: `src/bqpp/cli.py` (fill in `export`)
- Test: `tests/test_export.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `export_jsonl(db, out_path) -> int` writing one JSON object per question joined with its source document.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export.py
import json
from bqpp.db import Database
from bqpp.export import export_jsonl
from bqpp.models import Question, SourceDocument

def test_export_joins_source_document(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite"); db.init_schema()
    db.upsert_source_document(SourceDocument(id="d", source_id="hf-oab-exams",
                                             url="https://hf.co/x", fetched_at="t",
                                             kind="dataset", banca="FGV", exam_year=2018))
    db.upsert_question(Question(id="q1", source_doc_id="d", question_number="1",
                                format="mcq4", stem="s",
                                choices=[{"label": "A", "text": "a"}], answer_key="A"))
    out = tmp_path / "questions.jsonl"
    assert export_jsonl(db, out) == 1
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["id"] == "q1" and rec["source"]["banca"] == "FGV"
    assert rec["source"]["url"] == "https://hf.co/x"
    db.close()
```

```python
# tests/test_end_to_end.py
"""harvest (fixtures) -> classify -> vet -> curate, with a fake LLM. No network."""
import json
from pathlib import Path

from bqpp.classify import run_classify
from bqpp.config import load_settings, load_taxonomy
from bqpp.curate import run_curate
from bqpp.db import Database
from bqpp.harvest.hf_datasets import ingest_oab_exams
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import SourceDocument
from bqpp.vet import load_watchlist, run_vet

FIX = Path(__file__).parent / "fixtures"

def _router(call):
    """One fake backend serving both stages, keyed on prompt content."""
    if "classifying" in call["system"] or "taxonomy" in call["user"].lower():
        return json.dumps({"discipline": "direito-processual-penal",
                           "subtopic_ids": ["T1.2"], "difficulty": "medium", "note": ""})
    return json.dumps({"verdict": "flagged",
                       "reasons": [{"code": "resposta_mudou_mas_util",
                                    "detail": "Pacote Anticrime alterou o art. 316"}],
                       "pedagogy_note": "Ótima para discutir a mudança legislativa."})

def test_pipeline_produces_a_usable_shortlist(tmp_path, monkeypatch):
    settings = load_settings()
    monkeypatch.setattr(settings, "shortlist_dir", tmp_path / "shortlists")
    db = Database.connect(tmp_path / "t.sqlite"); db.init_schema()
    doc = SourceDocument(id="d", source_id="hf-oab-exams", url="https://hf.co/x",
                         fetched_at="t", kind="dataset", banca="FGV", carreira="oab",
                         certame="OAB 2015-01", exam_year=2015)
    db.upsert_source_document(doc)
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    assert ingest_oab_exams(rows, source_id="hf-oab-exams", doc=doc, db=db) == 2

    client = LLMClient(FakeBackend(_router))
    assert run_classify(db, client, load_taxonomy()) == 2
    assert run_vet(db, client, load_watchlist()) == 2

    written = run_curate(db, load_taxonomy(), settings, semester="2026.2",
                         subtopic_ids=["T1.2"])
    assert written["T1.2"] >= 1
    md = (settings.shortlist_dir / "2026-2" / "T1.2.md").read_text(encoding="utf-8")
    assert "⚠" in md and "resposta_mudou_mas_util" in md
    assert "bqpp use" in md and "<details>" in md
    db.close()
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `uv run pytest tests/test_export.py tests/test_end_to_end.py -v` — Expected: FAIL, `No module named 'bqpp.export'`

- [ ] **Step 3: Implement `src/bqpp/export.py`**

```python
"""JSONL export — the interchange format for anything downstream (spec §8)."""
from __future__ import annotations

import json
from pathlib import Path

from bqpp.db import Database


def export_jsonl(db: Database, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for q in db.iter_questions():
            doc = db.get_source_document(q.source_doc_id)
            record = q.model_dump()
            record["source"] = doc.model_dump() if doc else None
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    return n
```

- [ ] **Step 4: Wire the `export` CLI command**

```python
@app.command()
def export(db: DbOpt = None) -> None:
    """Write data/export/questions.jsonl."""
    from bqpp.export import export_jsonl

    settings = load_settings(); settings.ensure_dirs()
    database = _open_db(db)
    out = settings.export_dir / "questions.jsonl"
    console.print(f"exported [bold]{export_jsonl(database, out)}[/bold] questions to {out}")
    database.close()
```

- [ ] **Step 5: Write `README.md`** covering: what this is and who it is for; install (`uv sync`); the `.env` keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`); the four-command workflow (`harvest → classify → vet → curate`) with the `--dry-run` note; how to read a shortlist and record a pick with `bqpp use`; how to edit `config/taxonomy.yaml` and `config/law_watchlist.yaml`; how to switch LLM backend/models in `config/settings.toml`; and the M1 scope caveat (HF bootstrap only — FGV/Cebraspe PDFs are M2/M3, per `SPEC-questoes-pipeline.md`).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass, no network access in any test.

- [ ] **Step 7: Commit**

```bash
git add src/bqpp/export.py src/bqpp/cli.py README.md tests/test_export.py tests/test_end_to_end.py
git commit -m "feat: JSONL export, README, and end-to-end pipeline smoke test"
```

---

## M1 Definition of Done

Run with real API keys present:

```bash
uv run bqpp harvest -v
uv run bqpp classify -v
uv run bqpp vet -v
uv run bqpp curate --semester 2026.2
uv run bqpp stats
```

Expected: `shortlists/2026-2/` populated with plausible content for **≥15 subtopics**. If fewer, `bqpp stats` names the empty subtopics — that is the M2 work queue, not a bug in this milestone.
