"""Curated-manifest adapter for official provas that cannot be enumerated.

Spec §6 calls this "manually curated direct PDF links from MP/TJ transparency pages",
and that judgement holds up: URL construction is actively unsafe here. MPRS ordinal
substitution returns 404, its own provas index page 404s, and MPF's returns 401. So
every URL lives literally in `config/provas_manifest.yaml` with the date a human last
confirmed it, and adding a concurso is a YAML edit rather than an engineering task.

Each entry is self-contained: the answer grid is either inside the prova or in a
companion file the entry names, so there is no discovery step to break.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from bqpp.config import CONFIG_DIR
from bqpp.db import Database
from bqpp.harvest.http import Fetcher, FetchError
from bqpp.models import Question, SourceDocument, content_hash, question_id, source_doc_id
from bqpp.parse.columns import extract_columns
from bqpp.parse.objetiva import GridError, read_grid, segment_objetiva
from bqpp.parse.pdf import text_health

log = logging.getLogger(__name__)

REQUIRED = (
    "id", "url", "banca", "carreira", "certame", "exam_year",
    "format", "columns", "grid_style", "verified_on",
)


class ManifestError(ValueError):
    """The manifest is malformed. Raised at load, never halfway through a harvest."""


def load_manifest(path: Path | None = None) -> list[dict]:
    path = Path(path or CONFIG_DIR / "provas_manifest.yaml")
    if not path.exists():
        raise ManifestError(f"manifest not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("provas") or []
    for i, entry in enumerate(entries, start=1):
        name = entry.get("id") or f"entry #{i}"
        missing = [f for f in REQUIRED if not entry.get(f)]
        if missing:
            raise ManifestError(f"{name}: missing required field(s) {', '.join(missing)}")
    return entries


def _exam_document(*, source_id: str, entry: dict) -> SourceDocument:
    return SourceDocument(
        id=source_doc_id(f"{source_id}:{entry['id']}".encode()),
        source_id=source_id,
        url=entry["url"],
        fetched_at=datetime.now(UTC).isoformat(),
        kind="prova",
        banca=entry.get("banca"),
        carreira=entry.get("carreira"),
        certame=entry.get("certame"),
        exam_year=entry.get("exam_year"),
    )


def ingest_prova(
    prova_text: str,
    gabarito_text: str | None,
    *,
    entry: dict,
    source_id: str,
    db: Database,
    force: bool = False,
    seen_stems: dict[str, str] | None = None,
) -> int:
    """Segment one prova, join its answer grid, and write the questions."""
    items = segment_objetiva(prova_text)
    if not items:
        log.warning("%s: no questions segmented", entry["id"])
        return 0
    try:
        grid = read_grid(gabarito_text or prova_text, style=entry["grid_style"])
    except GridError as exc:
        # Refusing beats defaulting: a missing convention would ship wrong keys.
        log.error("%s: %s", entry["id"], exc)
        return 0

    seen = db.content_hashes() if seen_stems is None else seen_stems
    doc = _exam_document(source_id=source_id, entry=entry)
    db.upsert_source_document(doc, force=force)

    written = 0
    for item in items:
        number = int(item.number)
        if number not in grid:
            log.info("%s q%s: no entry in the answer grid — skipping", entry["id"], number)
            continue
        answer = grid[number]
        qid = question_id(doc.id, item.number)
        key = content_hash(item.stem, item.choices)
        if key in seen and seen[key] != qid:
            log.info("%s q%s: already in the corpus as %s — skipping duplicate",
                     entry["id"], number, seen[key][:12])
            continue
        q = Question(
            id=qid,
            source_doc_id=doc.id,
            question_number=item.number,
            format=entry["format"],
            stem=item.stem,
            choices=item.choices,
            answer_key=answer,
            nullified=answer is None,
        )
        if db.upsert_question(q, force=force):
            seen[key] = qid
            written += 1
    return written


def harvest_source(
    entry, db: Database, settings, *, dry_run: bool = False, force: bool = False,
    offline: bool = False,
) -> int:
    p = entry.params
    fetcher = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "provas",
        db=None if dry_run else db,
        min_interval=float(p.get("min_interval_seconds", 1.5)),
        offline=offline or dry_run,
    )
    manifest = load_manifest(CONFIG_DIR / p.get("manifest", "provas_manifest.yaml"))
    seen = {} if dry_run else db.content_hashes()
    total = 0
    for item in manifest:
        if dry_run:
            log.info("[dry-run] %s -> %s", item["id"], item["url"])
            continue
        try:
            prova = fetcher.get(item["url"])
            gabarito = fetcher.get(item["gabarito_url"]) if item.get("gabarito_url") else None
        except FetchError as exc:
            # The manifest is hand-maintained and will rot; one dead link must not
            # abandon the rest.
            log.error("%s: %s", item["id"], exc)
            continue
        columns = int(item.get("columns", 1))
        prova_text = extract_columns(prova.body, columns=columns)
        if text_health(prova_text) != "ok":
            log.warning("%s: unusable text layer — skipping", item["id"])
            continue
        gabarito_text = extract_columns(gabarito.body, columns=1) if gabarito else None
        total += ingest_prova(
            prova_text, gabarito_text, entry=item, source_id=entry.id, db=db,
            force=force, seen_stems=seen,
        )
    return total
