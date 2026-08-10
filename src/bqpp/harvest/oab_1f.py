"""Harvest adapter for the OAB 1ª-fase objective exam (M2.5).

The 1ª fase is 80 A-D questions across roughly 14 disciplines, published as two
PDFs on the same exam index pages `harvest/oab_site.py` already walks for the 2ª
fase: a caderno per tipo, and one gabarito file carrying all four tipos' answer
bands. Joining them on the item number is what this module does.

Four decisions live here, every one of them measured against the cached PDFs during
recon (`scripts/recon_1f.py`) rather than assumed:

  select_1f_artifacts   which two PDFs, of the dozens an exam's index page links,
                        are this tipo's caderno and its best available gabarito.

  is_criminal           which of a caderno's 80 questions — spread across those ~14
                        discipline blocks with no headings to bind on — are
                        criminal-law/criminal-procedure material worth keeping.

  choose_item_style     which of the source's candidate item anchors this particular
                        caderno numbers its questions with.

  read_tipo_grid        whether the answer grid can be trusted, cross-checked across
                        all four tipos on two independent axes.

The first two are pure. The last two are pure as well; only `harvest_source` fetches,
and it does so through `harvest/http.py`, which remains the only module in the project
that opens a socket. Everything configurable — the keyword list, the furniture to
strip, the tipo, the anchors to try, the excluded exams — lives in
`config/sources.yaml` under `oab-1f-penal`, never in this file.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from bqpp.db import Database
from bqpp.harvest.http import Fetcher, FetchError, normalise_url
from bqpp.harvest.oab_site import (
    Exam,
    IndexEntry,
    certame_for,
    parse_exam_ids,
    parse_exam_index,
)
from bqpp.models import Question, SourceDocument, content_hash, question_id, source_doc_id
from bqpp.parse.columns import extract_columns
from bqpp.parse.objetiva import GridError, ObjetivaItem, read_grid, segment_objetiva
from bqpp.parse.pdf import text_health

log = logging.getLogger(__name__)


def _fold(text: str) -> str:
    """Casefold and strip diacritics, so matching is accent- and case-insensitive."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------- artifact selection ---

# Every exam index page also carries 2ª-fase and administrative PDFs whose labels
# collide with the two 1ª-fase artifacts wanted here: an "Edital - Locais e Horário
# de Realização da Prova Objetiva (1ª fase)" and a "Resultado Definitivo (após
# recursos) - Prova Objetiva (1ª fase)" both mention the 1ª fase without being it
# (plan amendment E7). Dropped before matching, on folded text, so the accented
# spellings ("horário", "isenção", "inscrição") need no separate accent variant.
_ADMIN = re.compile(r"edital|resultado|comunicado|local|horario|isencao|inscricao|recurso")
_CADERNO_TIPO = re.compile(r"caderno\s+de\s+prova\s*[-–—]?\s*tipo\s*(\d)\b")
_GABARITO = re.compile(r"gabaritos?\b.*prova\s+objetiva")
_DEFINITIVO = re.compile(r"definitivos?")


@dataclass(frozen=True)
class Artifacts:
    caderno: IndexEntry
    gabarito: IndexEntry
    definitivo: bool


def select_1f_artifacts(entries: list[IndexEntry], *, tipo: int = 1) -> Artifacts | None:
    """The Tipo-N caderno and the best available 1ª-fase gabarito on one index page.

    Definitivo wins over preliminar; among equals — several preliminares occur,
    the OAB republishes a corrected key under the same label with an "- atualizado"
    suffix — the most recently published entry wins. Returns None if either
    artifact is missing, never a half-populated `Artifacts` (E7).

    A page can carry more than one full administration of the same numbered exam —
    a reaplicação for candidates who missed the original, each with its own
    caderno *and* its own gabarito (real case: exam 11553, a 2016 reaplicação in
    Salvador/BA). `cadernos[0]` and the gabarito `rank()` below are two independent
    selection rules with no shared notion of "which administration"; nothing
    guarantees they land on the same one. When more than one Tipo-N caderno is
    found this is refused rather than guessed at — silently pairing one
    administration's questions with another's answer key would make every answer
    wrong, which is worse than skipping the exam. A single caderno is never
    ambiguous this way because an administration without its own caderno set isn't
    a real administration; the gabarito side can safely carry a preliminar +
    definitivo pair (or several preliminares) for that one administration, which is
    the expected, common case `rank()` already resolves correctly.
    """
    real = [e for e in entries if not _ADMIN.search(_fold(e.label))]

    cadernos = [
        e for e in real if (m := _CADERNO_TIPO.search(_fold(e.label))) and int(m.group(1)) == tipo
    ]
    gabaritos = [e for e in real if _GABARITO.search(_fold(e.label))]
    if not cadernos or not gabaritos:
        return None
    if len(cadernos) > 1:
        return None

    def rank(entry: IndexEntry) -> tuple[bool, str]:
        return (
            bool(_DEFINITIVO.search(_fold(entry.label))),
            entry.date.isoformat() if entry.date else "",
        )

    best_gabarito = max(gabaritos, key=rank)
    return Artifacts(
        caderno=cadernos[0],
        gabarito=best_gabarito,
        definitivo=bool(_DEFINITIVO.search(_fold(best_gabarito.label))),
    )


# ---------------------------------------------------------------- the keyword gate ---

# How much inflection a keyword may absorb. Plain substring matching is wrong here
# (E9): `júri` folds to `juri` and then fires on *jurídico*, *jurisprudência* and
# *jurisdição*, and `pena` fires on *apenas* — between them they kept ten items of
# pure civil, tax and labour law on a hand-checked exam. Anchoring the match at a
# word start and allowing at most three trailing letters keeps the inflections that
# matter (crime/crimes, dolo/doloso, réu/réus) and drops both pathologies.
_SUFFIX_SLACK = 3


def _pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(_fold(keyword))}\w{{0,{_SUFFIX_SLACK}}}(?!\w)")


def is_criminal(item: ObjetivaItem, keywords: list[str], min_hits: int) -> bool:
    """Whether an objective item is criminal-law/criminal-procedure material.

    Scored over the stem AND every alternative, never the stem alone (E9) — the
    topic is frequently declared only in an alternative, the same rule the
    codebase already applies to comando + stem + rationale elsewhere (classify,
    vet). `keywords` and `min_hits` are professor-maintained config passed in by
    the caller (`config/sources.yaml`, Task 6) — nothing here hardcodes the list.
    """
    haystack = _fold(item.stem + "\n" + "\n".join(c["text"] for c in item.choices))
    hits = {k for k in keywords if _pattern(k).search(haystack)}
    return len(hits) >= min_hits


# ------------------------------------------------------------------- item anchors ---


def _longest_run(numbers: list[int]) -> int:
    """Length of the longest ascending run of consecutive integers."""
    best = current = 0
    previous: int | None = None
    for n in numbers:
        current = current + 1 if previous is not None and n == previous + 1 else 1
        previous = n
        best = max(best, current)
    return best


def choose_item_style(
    text: str, *, styles: Sequence[str], furniture: list[str] | None = None
) -> tuple[str, list[ObjetivaItem]]:
    """Segment under each candidate anchor and keep the reading that looks like a prova.

    The anchor is a property of the individual caderno, not of the source: 17 of the
    19 in-scope exams number a question with a bare numeral on its own line, and the
    XXVIII and XXIX (both 2019) write "Questão N" instead (E10). A single
    `item_style` in config cannot cover both.

    The alternative — a per-exam override map keyed by the OAB's internal exam id —
    was rejected: the map is silent by construction. A new exam that switches
    convention is not in the map, falls back to the source default, and segments to a
    handful of items or none at all; nothing fails, the exam is just quietly thin. The
    detection below has the opposite failure mode, in that a caderno that recovers
    nothing under either anchor recovers nothing under both and is reported as such.

    Ranked on the **longest contiguous run** first and the raw item count second. A
    run is the stronger signal: it says the candidates form a real question sequence
    rather than a scatter of numerals a page layout happened to leave on their own
    lines. Measured over all 19 cadernos the two axes agree everywhere and the margin
    is never close — the losing anchor scores (0, 0) on 17 exams and at most (25, 25)
    against a winner of (80, 80) on the other two.
    """
    best: tuple[tuple[int, int], str, list[ObjetivaItem]] = ((0, 0), "", [])
    for style in styles:
        items = segment_objetiva(text, furniture=furniture, item_style=style)
        numbers = sorted(int(i.number) for i in items)
        score = (_longest_run(numbers), len(items))
        if score > best[0]:
            best = (score, style, items)
    return best[1], best[2]


# ------------------------------------------------------------ the cross-tipo check ---

# Every OAB gabarito carries all four tipos of one exam back to back, and `read_grid`
# reads one of them. Reading the other three costs nothing and is the only signal
# available for the scoping residuals `parse.objetiva._tipo_blocks` documents and
# cannot close from inside a single block.
TIPOS: tuple[int, ...] = (1, 2, 3, 4)


def read_tipo_grid(
    gabarito_text: str, *, tipo: int, tipos: Sequence[int] = TIPOS
) -> dict[int, str | None]:
    """One tipo's answer grid, cross-checked against the other three.

    Two independent axes, because each on its own was proven insufficient by a
    different fix round of Task 3:

    **Entry count.** A dropped trailing band comes back as a short grid. It cannot be
    detected from inside one block — 60 contiguous answers look exactly like a
    60-question exam — but four blocks that disagree on how many questions the exam
    had are unambiguous.

    **Content divergence.** A merge keeps the count right and corrupts the letters.
    The four tipos are the same 80 questions in four shuffled orders, so two tipos
    that answer every shared item identically are not two tipos; one of them is the
    other, read twice. Measured across all 19 gabaritos, tipo 1 and tipo 2 differ on
    41 to 70 of 80 answers — never on none.

    Raises `GridError` on either, which the caller turns into a skipped exam. Never
    a repaired grid: a wrong answer key reaches a student as fact.
    """
    grids = {t: read_grid(gabarito_text, style="banded", tipo=t) for t in tipos}
    if tipo not in grids:
        raise GridError(f"tipo {tipo} was not among the tipos read ({sorted(grids)})")

    counts = {t: len(g) for t, g in grids.items()}
    if len(set(counts.values())) > 1:
        raise GridError(
            f"the tipos recovered different entry counts ({counts}) — one block is "
            f"missing a band, so the grid cannot be trusted"
        )

    others = [t for t in tipos if t != tipo]
    if others:
        other = others[0]
        shared = set(grids[tipo]) & set(grids[other])
        diverging = sum(1 for n in shared if grids[tipo][n] != grids[other][n])
        if not diverging:
            raise GridError(
                f"tipo {tipo} and tipo {other} answer all {len(shared)} shared items "
                f"identically — two tipos' bands have probably merged into one block"
            )
        log.debug("tipo %d and tipo %d diverge on %d of %d items",
                  tipo, other, diverging, len(shared))
    return grids[tipo]


# ----------------------------------------------------------------------- ingestion ---


# `format` is a promise about shape, so it is checked rather than asserted: an item
# that came back with five alternatives is not an mcq4 and is dropped instead of
# being stored under a label that misdescribes it. A format not listed here carries
# no shape promise and is left alone.
_CHOICES_PER_FORMAT: dict[str, int] = {"mcq4": 4, "mcq5": 5}


def _item_text(item: ObjetivaItem) -> str:
    return item.stem + " " + " ".join(c["text"] for c in item.choices)


def _exam_document(
    *, source_id: str, exam: Exam, artifacts: Artifacts, tipo: int, params: dict
) -> SourceDocument:
    """One `source_documents` row per (exam, tipo).

    The id is derived from (source, exam, tipo) rather than from the file bytes so a
    republished gabarito — a definitivo replacing the preliminary key this exam was
    first harvested under — updates the same rows under `--force` instead of
    duplicating the exam. The file's own sha256 lives in `harvest_manifest`.

    `exam_year` comes from the caderno's index entry and is load-bearing: the law
    watchlist only fires on questions whose year predates a change, so a null year
    silently disables vetting for all 80 of this exam's questions.
    """
    return SourceDocument(
        id=source_doc_id(f"{source_id}:{exam.id}:tipo{tipo}".encode()),
        source_id=source_id,
        url=normalise_url(artifacts.caderno.href),
        fetched_at=datetime.now(UTC).isoformat(),
        kind="prova",
        banca=params.get("banca"),
        carreira=params.get("carreira"),
        certame=certame_for(exam.label, None, fase="1ª fase"),
        exam_year=artifacts.caderno.date.year if artifacts.caderno.date else None,
    )


def ingest_caderno(
    caderno_text: str,
    gabarito_text: str,
    *,
    exam: Exam,
    artifacts: Artifacts,
    source_id: str,
    db: Database,
    params: dict,
    force: bool = False,
    seen: dict[str, str] | None = None,
) -> int:
    """Segment one Tipo-N caderno, join its answer grid, gate it, and write the rows.

    Nothing is written unless at least one question survives, so an exam that fails
    anywhere in here leaves no orphan `source_documents` row behind.
    """
    tipo = int(params.get("tipo", 1))
    styles = params.get("item_style") or ["punctuated"]
    if isinstance(styles, str):
        styles = [styles]
    style, items = choose_item_style(
        caderno_text, styles=styles, furniture=params.get("furniture")
    )
    if not items:
        log.warning("%s: no questions segmented under any of %s — skipping", exam.label, styles)
        return 0
    log.info("%s: %d items under item_style %r", exam.label, len(items), style)

    try:
        grid = read_tipo_grid(gabarito_text, tipo=tipo)
    except GridError as exc:
        # Refusing beats defaulting: a mis-read grid ships wrong answer keys.
        log.error("%s: %s — skipping the exam rather than keying it", exam.label, exc)
        return 0

    keywords = list(params.get("keep_keywords") or [])
    min_hits = int(params.get("min_keyword_hits", 2))
    fmt = params.get("format", "mcq4")
    expected_choices = _CHOICES_PER_FORMAT.get(fmt)

    eligible: list[ObjetivaItem] = []
    illegible = misshapen = off_grid = 0
    for item in items:
        # Per ITEM, never per document (E11): two exams carry an unmapped cover page
        # and 80 perfectly legible questions, and a document-level veto discards both.
        if text_health(_item_text(item)) != "ok":
            illegible += 1
            log.info("%s q%s: the item's own text layer is unusable — skipping",
                     exam.label, item.number)
            continue
        if expected_choices is not None and len(item.choices) != expected_choices:
            misshapen += 1
            log.info("%s q%s: %d alternatives, not %d — skipping rather than "
                     "storing it as %s", exam.label, item.number, len(item.choices),
                     expected_choices, fmt)
            continue
        if int(item.number) not in grid:
            off_grid += 1
            log.info("%s q%s: no entry in the answer grid — skipping",
                     exam.label, item.number)
            continue
        eligible.append(item)

    kept = [i for i in eligible if is_criminal(i, keywords, min_hits)]
    # Not optional: a silent gate reads as full coverage when it is not.
    log.info(
        "%s: keyword gate kept %d of %d eligible items (%d dropped as non-criminal); "
        "%d illegible, %d misshapen, %d absent from the grid",
        exam.label, len(kept), len(eligible), len(eligible) - len(kept),
        illegible, misshapen, off_grid,
    )
    if not kept:
        return 0

    if seen is None:
        seen = db.content_hashes()
    doc = _exam_document(
        source_id=source_id, exam=exam, artifacts=artifacts, tipo=tipo, params=params
    )
    db.upsert_source_document(doc, force=force)

    written = 0
    for item in kept:
        answer = grid[int(item.number)]
        qid = question_id(doc.id, item.number)
        key = content_hash(item.stem, item.choices)
        # A match on our own row is just a re-run and must not make --force a no-op.
        if key in seen and seen[key] != qid:
            log.info("%s q%s: already in the corpus as %s — skipping duplicate",
                     exam.label, item.number, seen[key][:12])
            continue
        q = Question(
            id=qid,
            source_doc_id=doc.id,
            question_number=item.number,
            format=fmt,
            stem=item.stem,
            choices=item.choices,
            answer_key=answer,
            # A preliminary gabarito is still subject to recursos. The question is
            # stored normally and marked, so vetting can surface the exposure rather
            # than passing a contestable key off as settled fact.
            answer_key_provisional=not artifacts.definitivo,
            nullified=answer is None,
        )
        if db.upsert_question(q, force=force):
            seen[key] = qid
            written += 1
    return written


def harvest_source(
    entry,
    db: Database,
    settings,
    *,
    dry_run: bool = False,
    force: bool = False,
    offline: bool = False,
) -> int:
    """Discover every exam, fetch its Tipo-N caderno and gabarito, and ingest them.

    One `Fetcher` for the whole run, so the ≤ 1 request/second rule (spec §6) is
    enforced once for the host rather than once per cache directory. That is a
    deliberate trade: the exam index pages are also in `oab_site`'s cache directory
    and reading them from there would save 47 GETs on a cold start, but two Fetchers
    means two independent pacers against the same host, and etiquette outranks 70
    seconds. The PDFs themselves are already keyed by URL hash under `oab_1f`, which
    is where the recon sweep left them, so the expensive half of the cache is warm.

    Every failure below is per-exam: a dead link, an unreadable grid or a caderno
    that segments to nothing costs that exam and nothing else.
    """
    p = entry.params
    fetcher = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "oab_1f",
        db=None if dry_run else db,
        min_interval=float(p.get("min_interval_seconds", 1.5)),
        # --dry-run means "do everything except write", and writing includes writing
        # to someone else's server logs.
        offline=offline or dry_run,
    )
    tipo = int(p.get("tipo", 1))
    min_year = int(p.get("min_exam_year", 0))
    excluded = {str(x) for x in (p.get("exclude_exam_ids") or ())}

    try:
        seed = fetcher.get(p["seed_url"]).body.decode("utf-8", "replace")
    except FetchError as exc:
        log.error("%s: %s", entry.id, exc)
        return 0
    exams = parse_exam_ids(seed)
    if not exams:
        log.error("%s: the index yielded no exams — the site layout may have changed", entry.id)
        return 0
    log.info("%s: %d exams on the index", entry.id, len(exams))

    seen = {} if dry_run else db.content_hashes()
    total = 0
    for exam in exams:
        if exam.id in excluded:
            log.info("%s: excluded by config (exclude_exam_ids)", exam.label)
            continue
        try:
            index_html = fetcher.get(
                p["exam_url_template"].format(exam_id=exam.id)
            ).body.decode("utf-8", "replace")
        except FetchError as exc:
            log.error("%s: %s", exam.label, exc)
            continue
        artifacts = select_1f_artifacts(parse_exam_index(index_html), tipo=tipo)
        if artifacts is None:
            log.info("%s: no unambiguous Tipo %d caderno + 1ª-fase gabarito pair",
                     exam.label, tipo)
            continue
        date = artifacts.caderno.date
        if date is None:
            log.info("%s: the caderno entry carries no date — exam_year would be null "
                     "and the law watchlist could never fire, skipping", exam.label)
            continue
        if date.year < min_year:
            log.info("%s: %d is before min_exam_year %d — skipping",
                     exam.label, date.year, min_year)
            continue
        if dry_run:
            log.info("[dry-run] %s (%d) -> %s + %s", exam.label, date.year,
                     artifacts.caderno.label, artifacts.gabarito.label)
            continue
        try:
            caderno = fetcher.get(artifacts.caderno.href)
            gabarito = fetcher.get(artifacts.gabarito.href)
        except FetchError as exc:
            log.error("%s: %s", exam.label, exc)
            continue
        caderno_text = extract_columns(caderno.body, columns=int(p.get("columns", 2)))
        # The gabarito is read whole-width on purpose: it is a full-page table of tipo
        # bands, and cropping it at the midpoint would split every band in half.
        gabarito_text = extract_columns(gabarito.body, columns=1)
        health = text_health(caderno_text)
        if health != "ok":
            # A warning, never a veto (E11): 12895 and 13817 report glyph_unmapped for
            # an unmapped cover page and carry 80 individually clean questions each.
            log.warning("%s: whole-document text health is %s — judging each item "
                        "on its own", exam.label, health)
        total += ingest_caderno(
            caderno_text, gabarito_text, exam=exam, artifacts=artifacts,
            source_id=entry.id, db=db, params=p, force=force, seen=seen,
        )
    return total
