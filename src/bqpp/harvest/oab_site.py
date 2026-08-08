"""Harvest adapter for the Conselho Federal da OAB's exam publication site.

The spec (§6) names this source `fgv-oab` and points it at oab.fgv.br. That site
no longer works: every `home.aspx?key=N` URL returns the same 7 kB ASP.NET shell
with a seccional dropdown and no PDF links at all. The OAB publishes the same
files — byte-identical — over plain GET, so the adapter is pointed there instead.
FGV remains the `banca`; the OAB is merely the host.

Discovery is two levels of GET, both configured in `config/sources.yaml`:

    seed page      -> 46 exam ids
    per-exam page  -> labelled links, one of which is the Direito Penal padrão

The padrão de respostas is self-contained — enunciado *and* the banca's own
gabarito comentado — so unlike the spec's plan there is no prova/gabarito pair to
join. That is the whole reason this milestone is small.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from bqpp.db import Database
from bqpp.harvest.http import Fetcher, FetchError, normalise_url
from bqpp.models import Question, SourceDocument, question_id, source_doc_id, stem_hash
from bqpp.parse.padrao import segment_padrao
from bqpp.parse.pdf import extract_text, text_health

log = logging.getLogger(__name__)

_OPTION = re.compile(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', re.S | re.I)
_ANCHOR = re.compile(r'<a\b[^>]*href="([^"]*\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")

# "Padrão de respostas [definitivo] (Direito Penal)" and its nine other spellings:
# with or without the article, parenthesised or dash-separated, and with the word
# "definitivo" attached to either noun. Equality matching would miss eight of ten.
_PADRAO = re.compile(
    r"padr[ãa]o\s+de\s+respostas?"          # "Padrão de resposta(s)"
    r"(?P<def1>\s+definitivos?)?"           # "...definitivo"
    r"\s*[-–—(]*\s*"
    r"(?P<def2>definitivos?\s*[-–—]?\s*)?"  # or "... - Definitivo- Penal"
    r"(?:direito\s+)?penal\b",
    re.I,
)
_VARIANT = re.compile(
    r"\((?:direito\s+)?penal\)\s*[-–—]\s*(?P<v>.+?)\s*$"     # "(Direito Penal) - Porto Alegre/RS"
    r"|(?:direito\s+)?penal\s*[-–—]\s*(?P<w>Reaplica[çc][ãa]o.+?|[A-Z][^-–—]*/[A-Z]{2})\s*$",
    re.I,
)


@dataclass(frozen=True)
class Exam:
    id: str
    label: str


@dataclass(frozen=True)
class IndexEntry:
    href: str
    label: str

    @property
    def date(self) -> date | None:
        """The publication date carried by the anchor text.

        This — not the /arquivos/YYYY/MM/ path segment — is where exam_year comes
        from. Pre-2019 files were rehomed on the CDN, so the path is the upload
        date and would silently backdate two thirds of the archive to 2019.
        """
        m = _DATE.match(self.label)
        if not m:
            return None
        d, mo, y = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    @property
    def variant(self) -> str | None:
        """Reaplicação / city suffix, e.g. "Porto Alegre/RS".

        These are separate applications of the same exam with their own questions,
        not duplicates to collapse.
        """
        m = _VARIANT.search(self.label)
        if not m:
            return None
        return (m.group("v") or m.group("w") or "").strip() or None


def parse_exam_ids(html: str) -> list[Exam]:
    """The seed page's <select> lists every exam. value="0" is the placeholder."""
    return [
        Exam(id=value, label=_text(label))
        for value, label in _OPTION.findall(html)
        if value != "0"
    ]


def parse_exam_index(html: str) -> list[IndexEntry]:
    return [IndexEntry(href=href.strip(), label=_text(label)) for href, label in _ANCHOR.findall(html)]


def select_penal_padrao(entries: list[IndexEntry]) -> tuple[IndexEntry, str] | None:
    """Pick this exam's Direito Penal padrão, preferring the definitivo.

    Returns the entry and which rung of the ladder it came from, so the choice is
    recorded as provenance rather than silently made: 34 exams publish a
    definitivo, 11 only a preliminary padrão, and one (the 47º, mid-cycle) neither.
    """
    padroes = [e for e in entries if _PADRAO.search(e.label)]
    if not padroes:
        return None
    definitivos = [e for e in padroes if "definitiv" in e.label.lower()]
    if definitivos:
        return definitivos[0], "definitivo"
    return padroes[0], "plain"


def _text(fragment: str) -> str:
    return html_mod.unescape(" ".join(_TAGS.sub(" ", fragment).split()))


# ---------------------------------------------------------------- ingestion

_EDITION = re.compile(r"^\s*(\S+)\s+EXAME\b", re.I)
_EDITION_TRAILING = re.compile(r"EXAME\s+DE\s+ORDEM\s+UNIFICADO\s+(\S+)\s*$", re.I)


def _certame(exam_label: str, variant: str | None) -> str:
    """"44º EXAME DE ORDEM UNIFICADO" -> "OAB 44º Exame (2ª fase)"."""
    m = _EDITION.match(exam_label)
    trailing = _EDITION_TRAILING.search(exam_label)
    if trailing:                     # "EXAME DE ORDEM UNIFICADO 2010.2"
        certame = f"OAB Exame {trailing.group(1)} (2ª fase)"
    elif m:
        certame = f"OAB {m.group(1)} Exame (2ª fase)"
    else:
        certame = f"OAB {exam_label} (2ª fase)"
    return f"{certame} — {variant}" if variant else certame


def _exam_document(
    *, source_id: str, exam: Exam, entry: IndexEntry, banca: str | None, carreira: str | None
) -> SourceDocument:
    """One source_documents row per exam.

    The id is derived from (source, exam, variant) rather than the file bytes so a
    republished padrão — a definitivo replacing a preliminary — updates in place
    under --force instead of duplicating the exam. The file's own sha256 lives in
    harvest_manifest, which is its proper home.

    exam_year is set here and it is load-bearing: the law watchlist only fires on
    questions whose year predates a change, so a null year silently disables
    vetting for the whole exam.
    """
    variant = entry.variant
    return SourceDocument(
        id=source_doc_id(f"{source_id}:{exam.id}:{variant or ''}".encode()),
        source_id=source_id,
        url=normalise_url(entry.href),
        fetched_at=datetime.now(UTC).isoformat(),
        kind="gabarito_justificado",
        banca=banca,
        carreira=carreira,
        certame=_certame(exam.label, variant),
        exam_year=entry.date.year if entry.date else None,
        local_path=None,
    )


def ingest_padrao(
    text: str,
    *,
    source_id: str,
    exam: Exam,
    entry: IndexEntry,
    rung: str,
    banca: str | None = None,
    carreira: str | None = None,
    db: Database,
    force: bool = False,
    seen_stems: dict[str, str] | None = None,
) -> int:
    """Segment one padrão and write its usable sections as questions."""
    sections = segment_padrao(text)
    if not sections:
        log.warning("%s: no section anchors — layout predates the convention, skipping", exam.label)
        return 0

    seen = db.stem_hashes() if seen_stems is None else seen_stems
    doc = _exam_document(
        source_id=source_id, exam=exam, entry=entry, banca=banca, carreira=carreira
    )
    db.upsert_source_document(doc, force=force)

    written = 0
    for section in sections:
        if not section.usable:
            log.info(
                "%s questão %s: enunciado did not extract (%d chars) — skipping rather than "
                "storing an empty stem", exam.label, section.number, len(section.stem),
            )
            continue
        key = stem_hash(section.stem)
        qid = question_id(doc.id, section.number)
        # Dedup guards against the same question arriving from *another* source
        # (exams 39º-44º are already held from maritaca-ai/oab-bench). A match on
        # our own row is just a re-run, and must not make --force a no-op.
        if key in seen and seen[key] != qid:
            log.info(
                "%s questão %s: already in the corpus as %s — skipping duplicate",
                exam.label, section.number, seen[key][:12],
            )
            continue
        q = Question(
            id=qid,
            source_doc_id=doc.id,
            question_number=section.number,
            format=section.format,
            stem=section.stem,
            choices=None,
            answer_key=None,
            answer_rationale=section.rationale,
        )
        if db.upsert_question(q, force=force):
            seen[key] = q.id
            written += 1
    if rung == "plain":
        log.info("%s: only a preliminary padrão was published (no definitivo)", exam.label)
    return written


def harvest_source(
    entry, db: Database, settings, *, dry_run: bool = False, force: bool = False
) -> int:
    """Discover every exam, fetch its Penal padrão, and ingest it."""
    p = entry.params
    fetcher = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "oab",
        db=None if dry_run else db,
        min_interval=float(p.get("min_interval_seconds", 1.5)),
    )
    exams = parse_exam_ids(fetcher.get(p["seed_url"]).body.decode("utf-8", "replace"))
    log.info("%s: %d exams on the index", entry.id, len(exams))

    seen = {} if dry_run else db.stem_hashes()
    total = 0
    for exam in exams:
        index_html = fetcher.get(
            p["exam_url_template"].format(exam_id=exam.id)
        ).body.decode("utf-8", "replace")
        chosen = select_penal_padrao(parse_exam_index(index_html))
        if not chosen:
            log.info("%s: no Direito Penal padrão published", exam.label)
            continue
        index_entry, rung = chosen
        if dry_run:
            log.info("[dry-run] %s -> %s (%s)", exam.label, index_entry.label, rung)
            continue
        try:
            pdf = fetcher.get(index_entry.href)
        except FetchError as exc:
            log.error("%s: %s", exam.label, exc)
            continue
        text = extract_text(pdf.body)
        health = text_health(text)
        if health != "ok":
            log.warning("%s: text layer is %s — skipping", exam.label, health)
            continue
        total += ingest_padrao(
            text, source_id=entry.id, exam=exam, entry=index_entry, rung=rung,
            banca=p.get("banca"), carreira=p.get("carreira"), db=db, force=force,
            seen_stems=seen,
        )
    return total
