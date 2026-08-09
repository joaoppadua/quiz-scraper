"""Pydantic models mirroring the SQLite schema (spec §8)."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field

# "manual" is a professor-authored seed (see seed.py), not a harvested document.
Kind = Literal["prova", "gabarito", "gabarito_justificado", "dataset", "manual"]
Format = Literal["mcq4", "mcq5", "certo_errado", "dissertativa", "peca"]
Discipline = Literal["direito-processual-penal", "other", "mixed"]
Carreira = Literal["oab", "magistratura", "mp", "delegado", "defensoria", "outra"]
VetStatus = Literal["unvetted", "ok", "flagged", "rejected"]
Difficulty = Literal["easy", "medium", "hard"]


def source_doc_id(payload: bytes) -> str:
    """Deterministic id for a source document: sha256 of its bytes."""
    return hashlib.sha256(payload).hexdigest()


def content_hash(stem: str, choices: list[dict[str, str]] | None = None) -> str:
    """Content key for cross-source deduplication.

    The same OAB 2ª-fase question reaches the corpus twice — once from
    maritaca-ai/oab-bench and once from the official padrão — with different line
    wrapping and sometimes different case. Normalise both away before hashing, and
    use a prefix: the tail of a stem varies with how the point values were typeset.

    The alternatives are folded in when present, because a stem is not always where
    the content lives: MPF writes "Assinale a opção correta:" as the stem of five
    different questions and puts the substance in the alternatives. Hashing the stem
    alone collapses them into one and silently drops four. A question with no
    alternatives hashes exactly as it did before, so the discursive dedup is unchanged.
    """
    normalised = " ".join(stem.split()).casefold()[:300]
    if choices:
        alternatives = " | ".join(" ".join((c.get("text") or "").split()) for c in choices)
        normalised += " || " + alternatives.casefold()[:300]
    return hashlib.sha256(normalised.encode()).hexdigest()


def stem_hash(stem: str) -> str:
    """Deprecated alias: `content_hash` with no alternatives."""
    return content_hash(stem)


def question_id(source_doc_id: str, question_number: str) -> str:
    """Deterministic id for a question: sha256(source_doc_id + question_number)."""
    return hashlib.sha256(f"{source_doc_id}:{question_number}".encode()).hexdigest()


class SourceDocument(BaseModel):
    id: str
    source_id: str
    url: str
    fetched_at: str  # ISO 8601
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
    # Shared context a question depends on but does not own. Cebraspe certo/errado
    # items hang off a "comando" paragraph governing 3-7 siblings; storing it here
    # rather than prefixing it to `stem` is what keeps stem_hash able to tell those
    # siblings apart (a real comando outruns the hash window).
    stem_context: str | None = None
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
        """The question as handed to the LLM — content only, no pipeline metadata.

        Deliberately excludes classification/vetting fields so a re-run cannot be
        anchored by a previous run's verdict.
        """
        return {
            "format": self.format,
            "stem_context": self.stem_context,
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
