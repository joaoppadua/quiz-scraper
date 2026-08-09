"""Harvest adapter for Cebraspe's public exam API.

Spec §6 assumes a hand-curated allow-list of concurso slugs scraped from HTML.
Cebraspe in fact exposes an unauthenticated JSON API — no auth, no cookies, no JS —
and its robots.txt disallows nothing, so discovery is three deterministic GETs:

    seed      apis.cebraspe.org.br/cebraspe/eventos/tipo/concursos/
    manifest  apis.cebraspe.org.br/cebraspe/eventos/{slug}
    file      cdn.cebraspe.org.br/concursos/{slug}/arquivos/{nomeArquivo}

M3 harvests one genre: the *combined caderno*, which carries item text, answer key
and the banca's justificativa in a single file. Only two legal certames publish it,
so `sources.yaml` names them explicitly rather than sweeping all 490 events.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from bqpp.db import Database
from bqpp.harvest.http import Fetcher, FetchError
from bqpp.models import Question, SourceDocument, content_hash, question_id, source_doc_id
from bqpp.parse.caderno import segment_caderno
from bqpp.parse.columns import extract_columns
from bqpp.parse.pdf import text_health

log = logging.getLogger(__name__)

CDN = "https://cdn.cebraspe.org.br/concursos/{slug}/arquivos/{name}"

# The genre marker. Neither field alone is sufficient: the 2026 caderno announces
# itself in the description while the 2019 one is described merely "PROVA OBJETIVA"
# and only its filename gives it away.
_COMBINED_DESC = re.compile(r"COM\s+JUSTIFICATIVA", re.I)
_COMBINED_NAME = re.compile(r"_C_JUST\.PDF$", re.I)


@dataclass(frozen=True)
class Certame:
    slug: str
    name: str


@dataclass(frozen=True)
class Artifact:
    name: str            # nomeArquivo, already carrying its extension
    description: str     # descricaoArquivo, free text


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").upper())
        if unicodedata.category(c) != "Mn"
    )


def parse_seed(body: bytes) -> list[Certame]:
    """Flatten the seed's fase groups into one list of certames.

    The response is a list of 4 fase groups each carrying an `eventos` array, not a
    flat list of events. Reading it as flat yields 4 and loses the catalogue.
    """
    groups = _loads(body)
    if not isinstance(groups, list):
        return []
    out: dict[str, Certame] = {}
    for group in groups:
        for event in (group.get("eventos") or []) if isinstance(group, dict) else []:
            slug = event.get("eventoURL")
            if not slug:
                continue
            out.setdefault(
                slug,
                Certame(
                    slug=slug,
                    name=(event.get("eventoNomeCompleto")
                          or event.get("eventoNomeAbreviado") or slug),
                ),
            )
    return list(out.values())


def parse_manifest(body: bytes) -> list[Artifact]:
    """Every file entry in a certame's manifest, wherever it is nested.

    An unknown slug answers 204 with an empty body, which must read as not-found
    rather than raising out of a JSON parse.
    """
    data = _loads(body)
    if not data:
        return []
    found: list[Artifact] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "nomeArquivo" in node:
                found.append(
                    Artifact(
                        name=node.get("nomeArquivo") or "",
                        description=(node.get("descricaoArquivo") or "").strip(),
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return [a for a in found if a.name]


def select_combined_caderno(artifacts: list[Artifact]) -> Artifact | None:
    """The one file carrying item, answer and justificativa together, if published."""
    for a in artifacts:
        if _COMBINED_DESC.search(_norm(a.description)) or _COMBINED_NAME.search(a.name):
            return a
    return None


def cdn_url(slug: str, artifact: Artifact) -> str:
    """Build the CDN URL.

    `tipoExtensaoArquivo` is deliberately ignored: it reads `_.pdf` while
    `nomeArquivo` already ends in `.pdf`, and joining them yields a 404.
    """
    return CDN.format(slug=slug, name=quote(artifact.name, safe=""))


def _loads(body: bytes) -> object:
    if not body or not body.strip():
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("cebraspe: unparseable response (%s)", exc)
        return None


# ---------------------------------------------------------------- ingestion

MIN_JUSTIFICATIVAS = 20


class CadernoRejected(RuntimeError):
    """The downloaded file is not the combined-caderno genre after all.

    Selection reads a free-text description and a filename; both have produced false
    positives. Failing loudly beats registering a certame with no questions in it.
    """


def _exam_document(*, source_id, certame, url, banca) -> SourceDocument:
    """One source_documents row per certame.

    exam_year comes from config and is load-bearing: the law watchlist only fires on
    questions whose year predates a change, so a null year disables vetting for the
    whole certame.
    """
    return SourceDocument(
        id=source_doc_id(f"{source_id}:{certame['slug']}".encode()),
        source_id=source_id,
        url=url,
        fetched_at=datetime.now(UTC).isoformat(),
        kind="gabarito_justificado",
        banca=banca,
        carreira=certame.get("carreira"),
        certame=certame.get("certame") or certame["slug"],
        exam_year=certame.get("exam_year"),
    )


def ingest_caderno(
    text: str,
    *,
    source_id: str,
    certame: dict,
    url: str,
    banca: str | None,
    db: Database,
    force: bool = False,
    seen_stems: dict[str, str] | None = None,
) -> int:
    """Segment one combined caderno and write its usable items as questions."""
    items = segment_caderno(text)
    if len(items) < MIN_JUSTIFICATIVAS:
        raise CadernoRejected(
            f"{certame['slug']}: only {len(items)} items with a justificativa "
            f"(expected at least {MIN_JUSTIFICATIVAS}) — this is not a combined caderno"
        )

    seen = db.content_hashes() if seen_stems is None else seen_stems
    doc = _exam_document(source_id=source_id, certame=certame, url=url, banca=banca)
    db.upsert_source_document(doc, force=force)

    written = 0
    for item in items:
        if not item.usable:
            log.info(
                "%s item %s: comando points back at narrative the item does not carry "
                "— skipping", certame["slug"], item.number,
            )
            continue
        qid = question_id(doc.id, item.number)
        key = content_hash(item.stem)
        if key in seen and seen[key] != qid:
            log.info("%s item %s: already in the corpus as %s — skipping duplicate",
                     certame["slug"], item.number, seen[key][:12])
            continue
        q = Question(
            id=qid,
            source_doc_id=doc.id,
            question_number=item.number,
            format="certo_errado",
            stem=item.stem,
            stem_context=item.comando,
            answer_key=item.answer_key,
            answer_rationale=item.rationale,
        )
        if db.upsert_question(q, force=force):
            seen[key] = qid
            written += 1
    return written


def harvest_source(
    entry, db: Database, settings, *, dry_run: bool = False, force: bool = False,
    offline: bool = False,
) -> int:
    """Fetch and ingest each allow-listed certame's combined caderno."""
    p = entry.params
    fetcher = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "cebraspe",
        db=None if dry_run else db,
        min_interval=float(p.get("min_interval_seconds", 1.5)),
        offline=offline or dry_run,
    )
    seen = {} if dry_run else db.content_hashes()
    total = 0
    for certame in p.get("certames") or []:
        slug = certame["slug"]
        try:
            manifest = fetcher.get(p["event_url_template"].format(slug=slug))
        except FetchError as exc:
            log.error("%s: %s", slug, exc)
            continue
        artifact = select_combined_caderno(parse_manifest(manifest.body))
        if artifact is None:
            log.warning("%s: no combined caderno published", slug)
            continue
        url = cdn_url(slug, artifact)
        if dry_run:
            log.info("[dry-run] %s -> %s", slug, artifact.description or artifact.name)
            continue
        try:
            pdf = fetcher.get(url)
        except FetchError as exc:
            log.error("%s: %s", slug, exc)
            continue
        text = extract_columns(pdf.body, columns=int(p.get("columns", 2)))
        health = text_health(text)
        if health != "ok":
            log.warning("%s: text layer is %s — skipping", slug, health)
            continue
        try:
            total += ingest_caderno(
                text, source_id=entry.id, certame=certame, url=url,
                banca=p.get("banca"), db=db, force=force, seen_stems=seen,
            )
        except CadernoRejected as exc:
            log.error("%s", exc)
    return total
