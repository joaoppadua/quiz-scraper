"""HuggingFace dataset adapters (bootstrap ingestion, no PDF parsing).

The ingest_* functions take plain `list[dict]` rather than a dataset handle so the
tests can feed committed fixtures and production can feed parquet.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bqpp.db import Database
from bqpp.models import Question, SourceDocument, question_id, source_doc_id

log = logging.getLogger(__name__)


def _choices_from_struct(raw: Any) -> list[dict[str, str]] | None:
    """oab_exams stores choices as {'text': [...], 'label': [...]} — parallel lists."""
    if not raw:
        return None
    if isinstance(raw, list):  # already row-wise
        return [{"label": c["label"], "text": c["text"]} for c in raw]
    labels, texts = raw.get("label") or [], raw.get("text") or []
    if len(labels) != len(texts):
        # A plain zip would silently drop the tail and produce a question whose
        # alternatives don't match its gabarito. Refuse the row instead.
        log.warning("choices mismatch: %d labels vs %d texts; skipping", len(labels), len(texts))
        return None
    return [
        {"label": lab, "text": txt} for lab, txt in zip(labels, texts, strict=True)
    ] or None


def _exam_document(
    parent: SourceDocument, *, certame: str, exam_year: int | None, key: str
) -> SourceDocument:
    """Derive a per-exam source document from the bundle the rows arrived in.

    A HuggingFace dataset is one file spanning dozens of certames, but the spec's
    provenance model is one document per exam — and the vetting stage keys the law
    watchlist off `exam_year`. Registering only the bundle would leave every question
    with a null year, silently disabling watchlist injection.
    """
    return SourceDocument(
        id=source_doc_id(f"{parent.id}:{key}".encode()),
        source_id=parent.source_id,
        url=parent.url,
        fetched_at=parent.fetched_at,
        kind="dataset",
        banca=parent.banca,
        carreira=parent.carreira,
        certame=certame,
        exam_year=exam_year,
        local_path=parent.local_path,
    )


def ingest_oab_exams(
    rows: list[dict],
    *,
    source_id: str,
    doc: SourceDocument,
    db: Database,
    force: bool = False,
    question_type_filter: list[str] | None = None,
) -> int:
    """`doc` is the dataset bundle; one child document is registered per exam_id."""
    allowed = set(question_type_filter or ["CRIMINAL-PROCEDURE", "CRIMINAL"])
    seen_exams: dict[str, SourceDocument] = {}
    written = 0
    for row in rows:
        if row.get("question_type") not in allowed:
            continue
        choices = _choices_from_struct(row.get("choices"))
        if not choices:
            log.warning("skipping %s: no choices", row.get("id"))
            continue

        exam_id = str(row["exam_id"])
        if exam_id not in seen_exams:
            try:
                year = int(row.get("exam_year"))
            except (TypeError, ValueError):
                year = None
            exam_doc = _exam_document(
                doc, certame=f"OAB {exam_id}", exam_year=year, key=exam_id
            )
            db.upsert_source_document(exam_doc, force=force)
            seen_exams[exam_id] = exam_doc
        exam_doc = seen_exams[exam_id]

        # Prefix with exam_id: question_number restarts at 1 in every exam.
        number = f"{exam_id}-{row['question_number']}"
        q = Question(
            id=question_id(exam_doc.id, number),
            source_doc_id=exam_doc.id,
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
    """Official examiner guideline, one entry per sub-item, at choices[0].turns."""
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


def ingest_oab_bench(
    question_rows: list[dict],
    guideline_rows: list[dict],
    *,
    source_id: str,
    doc: SourceDocument,
    db: Database,
    force: bool = False,
    category_suffix_filter: list[str] | None = None,
) -> int:
    suffixes = tuple(category_suffix_filter or ["direito_penal"])
    guidelines = {
        g["question_id"]: g for g in guideline_rows if g.get("model_id") == "guidelines"
    }
    seen_exams: dict[str, SourceDocument] = {}
    written = 0
    for row in question_rows:
        if not row.get("category", "").endswith(suffixes):
            continue
        qid_raw = row["question_id"]
        is_peca = qid_raw.endswith("peca_profissional")

        # category is "{exam_number}_{discipline}". The dataset carries no year
        # column, so exam_year stays null and the watchlist simply isn't injected
        # for these — they are recent 2a-fase items where that matters least.
        exam_no = row["category"].split("_", 1)[0]
        if exam_no not in seen_exams:
            exam_doc = _exam_document(
                doc, certame=f"OAB {exam_no}º Exame (2ª fase)", exam_year=None, key=exam_no
            )
            db.upsert_source_document(exam_doc, force=force)
            seen_exams[exam_no] = exam_doc
        exam_doc = seen_exams[exam_no]

        q = Question(
            id=question_id(exam_doc.id, qid_raw),
            source_doc_id=exam_doc.id,
            question_number=qid_raw,
            # peças are flagged but their internal structure is out of scope (spec §2)
            format="peca" if is_peca else "dissertativa",
            stem=(
                row["statement"].strip()
                if is_peca
                else _stem_with_subitems(row["statement"], row.get("turns") or [])
            ),
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
    all_files = [
        f for f in api.list_repo_files(dataset, repo_type="dataset") if f.endswith(".parquet")
    ]
    files = [f for f in all_files if f"/{config}/" in f and split in f]
    if not files:
        files = [f for f in all_files if config in f] or all_files
    target = raw_dir / dataset.replace("/", "__")
    target.mkdir(parents=True, exist_ok=True)
    return [
        Path(hf_hub_download(dataset, f, repo_type="dataset", local_dir=target)) for f in files
    ]


def _read_rows(paths: list[Path]) -> list[dict]:
    import pyarrow.parquet as pq

    rows: list[dict] = []
    for p in paths:
        rows.extend(pq.read_table(p).to_pylist())
    return rows


def harvest_source(
    entry, db: Database, settings, *, dry_run: bool = False, force: bool = False
) -> int:
    """Download, register provenance, and ingest one hf_datasets source entry."""
    p = entry.params
    dataset = p["dataset"]
    config = p.get("config", "default")
    split = p.get("split", "train")

    paths = _download_parquet(dataset, config, split, settings.raw_dir)
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    doc = SourceDocument(
        id=source_doc_id(digest.digest()),
        source_id=entry.id,
        url=f"https://huggingface.co/datasets/{dataset}",
        fetched_at=datetime.now(UTC).isoformat(),
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
        return ingest_oab_bench(
            rows, _read_rows(g_paths), source_id=entry.id, doc=doc, db=db, force=force,
            category_suffix_filter=p.get("category_suffix_filter"),
        )
    return ingest_oab_exams(
        rows, source_id=entry.id, doc=doc, db=db, force=force,
        question_type_filter=p.get("question_type_filter"),
    )
