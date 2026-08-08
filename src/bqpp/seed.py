"""Professor-authored questions for subtopics no public exam covers.

T1.1 (princípios das medidas cautelares) and T3.3 (standards probatórios) have
zero candidates after M2, and the OAB exam is unlikely ever to supply them —
they are doctrinal topics objective exams avoid. The durable fix is M3, where
Cebraspe's magistratura and delegado provas do test them. This unblocks the
course meanwhile.

A seed is ingested *pre-classified*: the professor already knows which subtopic
his own question belongs to, and an LLM must not overrule him. It is not
pre-vetted, so the law watchlist still applies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bqpp.config import CONFIG_DIR, Taxonomy
from bqpp.db import Database
from bqpp.models import Question, SourceDocument, question_id, source_doc_id

log = logging.getLogger(__name__)


class SeedError(ValueError):
    """A seed entry is malformed. Named loudly rather than silently dropped."""


@dataclass(frozen=True)
class Seed:
    key: str
    raw: dict[str, Any] = field(repr=False)


def load_seeds(path: Path | None = None) -> list[Seed]:
    """Read the seed file. An absent or empty file is normal, not an error."""
    path = Path(path or CONFIG_DIR / "seed_questions.yaml")
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("questions") or []
    return [Seed(key=str(e.get("key") or f"seed-{i}"), raw=e) for i, e in enumerate(entries)]


def ingest_seeds(
    seeds: list[Seed],
    *,
    db: Database,
    taxonomy: Taxonomy,
    force: bool = False,
) -> int:
    written = 0
    for seed in seeds:
        e = seed.raw
        stem = (e.get("stem") or "").strip()
        if not stem:
            raise SeedError(f"seed {seed.key!r}: 'stem' is required and must not be empty")
        subtopics = e.get("subtopic_ids") or []
        if not subtopics:
            raise SeedError(f"seed {seed.key!r}: 'subtopic_ids' is required")
        bad = taxonomy.validate_ids(list(subtopics))
        if bad:
            raise SeedError(
                f"seed {seed.key!r}: {bad} are not ids in config/taxonomy.yaml"
            )

        doc = SourceDocument(
            id=source_doc_id(f"manual-seed:{seed.key}".encode()),
            source_id="manual-seed",
            url=e.get("source_url") or "manual",
            fetched_at=datetime.now(UTC).isoformat(),
            kind="manual",
            banca=e.get("banca"),
            carreira=e.get("carreira") or "outra",
            certame=e.get("certame"),
            # A seed with no year is invisible to the law watchlist, exactly as a
            # harvested question would be. Default to the year it was written.
            exam_year=e.get("exam_year"),
        )
        db.upsert_source_document(doc, force=True)

        q = Question(
            id=question_id(doc.id, seed.key),
            source_doc_id=doc.id,
            question_number=seed.key,
            format=e.get("format") or "dissertativa",
            stem=stem,
            choices=e.get("choices"),
            answer_key=e.get("answer_key"),
            answer_rationale=e.get("answer_rationale"),
            discipline="direito-processual-penal",
            subtopic_ids=list(subtopics),
            difficulty=e.get("difficulty"),
            classified_note=e.get("note") or "questão autoral do professor",
            classify_model="manual",
            classified_at=datetime.now(UTC).isoformat(),
        )
        if db.upsert_question(q, force=force):
            written += 1
        else:
            log.info("seed %s already in the corpus; use --force to re-apply edits", seed.key)
    return written
