"""Curation stage: deterministic ranking + self-contained markdown shortlists (spec §11).

No LLM runs here. The pipeline never picks the question used in class — it shortlists,
and the professor chooses.
"""

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


def rank_candidates(
    candidates: list[tuple[Question, SourceDocument | None]],
    ranking: RankingSettings,
    seen_carreiras: set[str] | None = None,
) -> list[tuple[Question, SourceDocument | None]]:
    seen = seen_carreiras or set()

    def key(item: tuple[Question, SourceDocument | None]) -> tuple:
        q, doc = item
        carreira = (doc.carreira if doc else None) or "outra"
        # tie-break: spread carreiras across the semester's shortlists
        diversity = 1.0 if carreira not in seen else 0.0
        return (-(score(q, doc, ranking) + diversity), q.id)

    return sorted(candidates, key=key)


def _fmt_choices(q: Question) -> str:
    if not q.choices:
        return ""
    return "\n".join(f"- **{c['label']})** {c['text']}" for c in q.choices)


def render_shortlist(
    subtopic_id: str,
    label: str,
    ranked: list[tuple[Question, SourceDocument | None]],
    *,
    semester: str,
) -> str:
    """Self-contained markdown: readable on GitHub or Drive with no other context."""
    out = [
        f"# {subtopic_id} — {label}",
        "",
        f"Semestre **{semester}** · gerado em {datetime.now(timezone.utc).date().isoformat()}",
        "",
        "> Escolha uma questão e registre com o comando indicado ao final de cada entrada.",
        "",
    ]
    if not ranked:
        out += [
            "## Nenhum candidato",
            "",
            "Nenhuma questão vetada foi classificada neste subtópico. "
            "Amplie o corpus (M2/M3) ou revise a taxonomia.",
            "",
        ]
        return "\n".join(out)

    for i, (q, doc) in enumerate(ranked, start=1):
        prov = " · ".join(
            filter(
                None,
                [
                    doc.banca if doc else None,
                    doc.certame if doc else None,
                    str(doc.exam_year) if doc and doc.exam_year else None,
                    (doc.carreira or "").upper() if doc and doc.carreira else None,
                ],
            )
        )
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
        out += [
            f"*Fonte: {prov or 'desconhecida'} · {doc.url if doc else ''}*",
            "",
            "```bash",
            f"bqpp use {q.id} --semester {semester} --subtopic {subtopic_id}",
            "```",
            "",
            "---",
            "",
        ]
    return "\n".join(out)


def run_curate(
    db: Database,
    taxonomy: Taxonomy,
    settings: Settings,
    *,
    semester: str,
    subtopic_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
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


def record_use(
    db: Database, question_id: str, semester: str, subtopic: str, note: str | None = None
) -> None:
    db.record_usage(
        UsageEntry(
            question_id=question_id,
            semester=semester,
            subtopic_id=subtopic,
            used_at=datetime.now(timezone.utc).isoformat(),
            note=note,
        )
    )
