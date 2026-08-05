"""LLM classification stage (spec §9.3). Tier: fast."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from bqpp.config import PROJECT_ROOT, Taxonomy
from bqpp.db import Database
from bqpp.llm.base import LLMError
from bqpp.llm.client import LLMClient
from bqpp.models import Question

log = logging.getLogger(__name__)
PROMPT_PATH = PROJECT_ROOT / "prompts" / "classify.md"

# additionalProperties:false plus all-keys-required keeps this valid for OpenAI
# strict mode; to_gemini_schema() trims what Gemini rejects.
CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "discipline": {
            "type": "string",
            "enum": ["direito-processual-penal", "other", "mixed"],
        },
        "subtopic_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
        "note": {"type": "string"},
    },
    "required": ["discipline", "subtopic_ids", "difficulty", "note"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a careful Brazilian legal-education assistant. You classify exam questions "
    "against a fixed taxonomy and return strict JSON. Never translate or alter the question."
)


def build_prompt(question: Question, taxonomy: Taxonomy) -> tuple[str, str]:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    user = template.replace("{taxonomy_yaml}", taxonomy.as_prompt_yaml()).replace(
        "{question_json}",
        json.dumps(question.to_prompt_payload(), ensure_ascii=False, indent=1),
    )
    return SYSTEM, user


def classify_question(question: Question, *, client: LLMClient, taxonomy: Taxonomy) -> dict:
    """Classify one question.

    The schema can only enforce "array of strings" — it cannot enforce that the
    strings are real taxonomy ids. So ids are checked in code after the call and,
    per spec §9.3, retried once with the error before falling back to unclassified.
    """
    system, user = build_prompt(question, taxonomy)
    result = client.complete_json(
        system=system, user=user, json_schema=CLASSIFY_SCHEMA,
        model_tier="fast", stage="classify",
    )
    bad = taxonomy.validate_ids(result["subtopic_ids"])
    if bad:
        retry_user = (
            f"{user}\n\n---\nYour previous answer used subtopic ids that do not exist: "
            f"{bad}. Use only ids from the taxonomy above, or return an empty list."
        )
        result = client.complete_json(
            system=system, user=retry_user, json_schema=CLASSIFY_SCHEMA,
            model_tier="fast", stage="classify",
        )
        bad = taxonomy.validate_ids(result["subtopic_ids"])
        if bad:
            log.warning(
                "question %s: model insisted on invalid ids %s; storing unclassified",
                question.id, bad,
            )
            note = (result.get("note") or "").strip()
            result = {
                **result,
                "subtopic_ids": [],
                "note": f"invalid subtopic ids returned by model: {bad}. {note}".strip(),
            }
    return result


def run_classify(
    db: Database,
    client: LLMClient,
    taxonomy: Taxonomy,
    *,
    only_unclassified: bool = True,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    questions = list(db.iter_questions(unclassified=only_unclassified and not force))
    if limit:
        questions = questions[:limit]
    done = 0
    for q in questions:
        try:
            result = classify_question(q, client=client, taxonomy=taxonomy)
        except LLMError as exc:
            # One bad question must not abandon the other 200.
            log.error("classification failed for %s: %s", q.id, exc)
            continue
        if dry_run:
            log.info(
                "[dry-run] %s -> %s %s", q.id[:12], result["discipline"], result["subtopic_ids"]
            )
            done += 1
            continue
        db.update_classification(
            q.id,
            discipline=result["discipline"],
            subtopic_ids=result["subtopic_ids"],
            difficulty=result["difficulty"],
            classified_note=result.get("note") or None,
            classify_model=client.backend.name,
            classified_at=datetime.now(UTC).isoformat(),
        )
        done += 1
    return done
