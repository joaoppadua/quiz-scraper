"""Vetting stage: cheap rules first, then the strong-tier LLM (spec §10)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
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
                    "code": {
                        "type": "string",
                        "enum": [
                            "desatualizada",
                            "resposta_mudou_mas_util",
                            "ambigua",
                            "decoreba",
                            "outros",
                        ],
                    },
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

SYSTEM = (
    "You are a Brazilian criminal-procedure professor vetting exam questions for classroom "
    "use. Be precise about what changed in the law and why it matters. Return strict JSON."
)

# A question whose answer changed with the law is teaching material, not garbage:
# it is exactly the kind of thing worth resolving with the class (spec §10.2).
_SOFTENING_CODE = "resposta_mudou_mas_util"

# Rule-derived reasons that pull an LLM verdict of "ok" down to "flagged": the question
# is still usable, but something about its answer key is not settled fact and the
# professor needs the warning banner (spec §10.2 for no_gabarito; same reasoning for a
# preliminary key still open to recursos).
_ESCALATING_CODES = frozenset({"no_gabarito", "gabarito_preliminar"})


class WatchlistEntry(BaseModel):
    id: str
    change: str
    effective: date
    affects: list[str]


def load_watchlist(path: Path | None = None) -> list[WatchlistEntry]:
    path = path or CONFIG_DIR / "law_watchlist.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [WatchlistEntry(**e) for e in raw["watchlist"]]


def apply_rules(
    question: Question,
    watchlist: list[WatchlistEntry],
    *,
    exam_year: int | None = None,
) -> tuple[VetStatus | None, list[VetReason], list[WatchlistEntry]]:
    """Return (terminal_status_or_None, reasons, watchlist entries to inject).

    A terminal status short-circuits the LLM call entirely.
    """
    if question.nullified:
        return "rejected", [VetReason(code="anulada", detail="questão anulada pela banca")], []

    reasons: list[VetReason] = []
    if question.format in ("mcq4", "mcq5", "certo_errado") and not question.answer_key:
        reasons.append(
            VetReason(code="no_gabarito", detail="sem gabarito alinhado para questão objetiva")
        )
    if question.answer_key_provisional:
        reasons.append(
            VetReason(
                code="gabarito_preliminar",
                detail="gabarito preliminar, sujeito a alteração após recursos",
            )
        )

    matched: list[WatchlistEntry] = []
    if exam_year is not None:
        subs = set(question.subtopic_ids)
        matched = [
            e for e in watchlist if exam_year < e.effective.year and subs & set(e.affects)
        ]
    return None, reasons, matched


def build_prompt(question: Question, entries: list[WatchlistEntry]) -> tuple[str, str]:
    if entries:
        block = "## Watchlist (mandatory considerations)\n\n" + "\n".join(
            f"- **{e.id}** (vigente desde {e.effective.isoformat()}): {e.change.strip()}"
            for e in entries
        )
    else:
        block = ""
    user = (
        PROMPT_PATH.read_text(encoding="utf-8")
        # local wall-clock year is the right one here: the prompt asks "is this
        # still correct today?" from the professor's calendar, not UTC's
        .replace("{current_year}", str(date.today().year))  # noqa: DTZ011
        .replace("{watchlist_block}", block)
        .replace(
            "{question_json}",
            json.dumps(question.to_prompt_payload(), ensure_ascii=False, indent=1),
        )
    )
    return SYSTEM, user


def vet_question(
    question: Question, *, client: LLMClient, entries: list[WatchlistEntry]
) -> dict:
    system, user = build_prompt(question, entries)
    return client.complete_json(
        system=system, user=user, json_schema=VET_SCHEMA, model_tier="strong", stage="vet"
    )


def _merge_verdict(
    rule_reasons: list[VetReason], result: dict
) -> tuple[VetStatus, list[VetReason]]:
    reasons = list(rule_reasons) + [VetReason(**r) for r in result.get("reasons", [])]
    verdict: VetStatus = result["verdict"]
    if verdict == "rejected" and any(r.code == _SOFTENING_CODE for r in reasons):
        verdict = "flagged"
    if verdict == "ok" and any(r.code in _ESCALATING_CODES for r in rule_reasons):
        verdict = "flagged"
    return verdict, reasons


def run_vet(
    db: Database,
    client: LLMClient,
    watchlist: list[WatchlistEntry],
    *,
    only_unvetted: bool = True,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    questions = list(db.iter_questions(unvetted=only_unvetted and not force))
    if limit:
        questions = questions[:limit]
    done = 0
    for q in questions:
        doc = db.get_source_document(q.source_doc_id)
        terminal, rule_reasons, entries = apply_rules(
            q, watchlist, exam_year=doc.exam_year if doc else None
        )
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
        db.update_vetting(
            q.id,
            vet_status=status,
            vet_reasons=reasons,
            pedagogy_note=note,
            vet_model=client.backend.name,
            vetted_at=datetime.now(UTC).isoformat(),
        )
        done += 1
    return done
