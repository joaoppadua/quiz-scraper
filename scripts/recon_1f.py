"""One-shot recon sweep over the OAB 1ª-fase objective provas (M2.5, Task 1).

This is a survey, not pipeline code. It measures — across all 19 in-scope exams —
what the existing parsers actually recover, so Task 2 and Task 3 can be written
against numbers instead of an extrapolation from the single exam probed so far.

Nothing here is imported by `src/bqpp`. One prototype still lives in this file:

  * the **banded grid reader** is prototyped in `read_banded` below. The real one
    is Task 3's job, in `parse/objetiva.py`, section-scoped by construction.

The item anchor used to be prototyped here too, by monkey-patching
`objetiva._ITEM`, before Task 2 shipped the real, selectable `item_style=`
kwarg on `segment_objetiva` (and made `_CHOICE`'s opening parenthesis optional
globally, so there is no separate "tolerant marker" mode to patch in either).
This file now calls that shipped API directly — the survey exercises production
code, not a stand-in for it.

Network etiquette is not reimplemented: every byte comes through
`harvest.http.Fetcher`, the only module in the project that opens a socket. Index
pages are read from the M2 cache in offline mode; only the 38 PDFs are fetched.

Run with `uv run python scripts/recon_1f.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bqpp.config import PROJECT_ROOT, load_settings
from bqpp.harvest.http import Fetcher, FetchError
from bqpp.harvest.oab_site import IndexEntry, parse_exam_ids, parse_exam_index
from bqpp.parse import objetiva
from bqpp.parse.columns import extract_columns
from bqpp.parse.objetiva import ANNULMENT_TOKENS, GridError
from bqpp.parse.pdf import text_health

SEED_URL = "https://examedeordem.oab.org.br/EditaisProvas?NumeroExame=0"
EXAM_URL = "https://examedeordem.oab.org.br/EditaisProvas?NumeroExame={exam_id}"
MIN_YEAR = 2019
TIPO = 1

# ---------------------------------------------------------------- discovery ---

# Administrative artifacts whose labels collide with the two we want: an
# "Edital - Locais e Horário de Realização da Prova Objetiva (1ª fase)" and a
# "Resultado Definitivo (após recursos) - Prova Objetiva (1ª fase)" both contain
# "Prova Objetiva (1ª fase)" and neither is an answer key.
_ADMIN = re.compile(
    r"edital|resultado|comunicado|local|hor[áa]rio|isen[çc][ãa]o|inscri[çc][ãa]o|recurso",
    re.I,
)
_CADERNO_TIPO = re.compile(r"caderno\s+de\s+prova\s*[-–—]?\s*tipo\s*(\d)\b", re.I)
_GABARITO = re.compile(r"gabaritos?\b[^|]*prova\s+objetiva", re.I)
_DEFINITIVO = re.compile(r"definitivos?", re.I)


@dataclass
class Artifacts:
    caderno: IndexEntry | None = None
    gabarito: IndexEntry | None = None
    definitivo: bool = False
    gabarito_count: int = 0


def select_1f_artifacts(entries: list[IndexEntry], *, tipo: int = TIPO) -> Artifacts:
    """The Tipo-N caderno and the best available 1ª-fase gabarito on one index page.

    Definitivo wins over preliminar; among equals the most recently published entry
    wins, because the OAB republishes a corrected key under the same label
    ("... - retificado em 19.11.2018").
    """
    real = [e for e in entries if not _ADMIN.search(e.label)]

    cadernos = [
        e for e in real if (m := _CADERNO_TIPO.search(e.label)) and int(m.group(1)) == tipo
    ]
    gabaritos = [e for e in real if _GABARITO.search(e.label)]

    def rank(entry: IndexEntry) -> tuple[int, str]:
        return (1 if _DEFINITIVO.search(entry.label) else 0, entry.date.isoformat() if entry.date else "")

    best = max(gabaritos, key=rank) if gabaritos else None
    return Artifacts(
        caderno=cadernos[0] if cadernos else None,
        gabarito=best,
        definitivo=bool(best and _DEFINITIVO.search(best.label)),
        gabarito_count=len(gabaritos),
    )


def discover(fetcher: Fetcher) -> list[tuple[str, Artifacts]]:
    """Every exam id from the cached seed page, paired with its cached index page."""
    seed = fetcher.get(SEED_URL).body.decode("utf-8", "replace")
    found: list[tuple[str, Artifacts]] = []
    for exam in parse_exam_ids(seed):
        try:
            page = fetcher.get(EXAM_URL.format(exam_id=exam.id)).body
        except FetchError as exc:
            print(f"  ! index page for exam {exam.id} unavailable: {exc}", file=sys.stderr)
            continue
        art = select_1f_artifacts(parse_exam_index(page.decode("utf-8", "replace")))
        if art.caderno is not None:
            found.append((exam.id, art))
    return found


# ---------------------------------------------------------- item anchors ---

# Two anchor conventions occur in the 19 cadernos, and the sweep reports which one
# each exam uses rather than assuming the 43º's.
#
#   bare      the question number alone on its own line          (17 of 19 exams)
#   questao   "Questão N" on its own line                        (XXVIII and XXIX, 2019)
#
# Both are `segment_objetiva`'s real, shipped `item_style` values (Task 2); this
# sweep just tries each and keeps whichever recovers the most items. The
# alternative marker (`(A)` vs `A)`, mixed inside a single caderno on the 38º) is
# no longer a separate mode to select either — `_CHOICE`'s opening parenthesis is
# optional unconditionally in the shipped module.
ANCHOR_STYLES: tuple[str, ...] = ("bare", "questao")


def best_anchor(text: str) -> tuple[str, list[objetiva.ObjetivaItem]]:
    """The anchor that recovers the most items, and what it recovered."""
    best: tuple[str, list[objetiva.ObjetivaItem]] = ("none", [])
    for style in ANCHOR_STYLES:
        items = objetiva.segment_objetiva(text, item_style=style)
        if len(items) > len(best[1]):
            best = (style, items)
    return best


# ------------------------------------------------------- banded grid reader ---

_NUM_ROW = re.compile(r"^[ \t]*\d{1,3}(?:[ \t]+\d{1,3})+[ \t]*$")
_TOKEN = r"(?:ANULAD[AO]|[A-EXN*])"
_LETTER_ROW = re.compile(rf"^[ \t]*{_TOKEN}(?:[ \t]+{_TOKEN})*[ \t]*$")
# The tipo heading has four spellings across the 19 gabaritos:
#   "PROVA TIPO 1"                                       (44º, 2025-09)
#   "43º EXAME DE ORDEM - PROVA TIPO 1"                  (42º, 43º, 45º, 46º)
#   "XXVIII EXAME DE ORDEM UNIFICADO – TIPO 1 – BRANCO"  (2019 .. 2023-03)
#   "XXXIX EXAME DE ORDEM UNIFICADO - PROVA 1"           (39º, 2023-11)
# Nothing is common to all four except the tipo token itself, so the pattern binds on
# that and rejects prose by requiring a short line: the correspondence table's
# explanation ("...a numeração da questão na prova de Tipo 1 e sua correspondência...")
# is the one place where a longer line would otherwise match.
_TIPO_HEAD = re.compile(
    r"^[ \t]*(?=[^\n]{1,60}$)[^\n]*?\b(?:PROVA[ \t]+TIPO|TIPO|PROVA)[ \t]+0?(\d)\b[^\n]*$",
    re.M | re.I,
)


def read_banded(text: str, *, tipo: int) -> dict[int, str | None]:
    """Prototype of the third grid convention: a row of item numbers, then a row of
    the same many letters directly beneath it.

    Scoping to a tipo is mandatory and is not best-effort. One OAB gabarito file
    carries all four tipos; an unscoped read lets Tipo 4 overwrite Tipo 1 and ships
    four-fifths wrong answer keys.
    """
    heads = list(_TIPO_HEAD.finditer(text))
    wanted = [h for h in heads if int(h.group(1)) == tipo]
    if not wanted:
        raise GridError(f"no tipo {tipo} heading among {[h.group(0).strip() for h in heads]}")
    head = wanted[0]
    after = [h for h in heads if h.start() > head.end()]
    scope = text[head.end() : after[0].start()] if after else text[head.end() :]

    lines = scope.splitlines()
    grid: dict[int, str | None] = {}
    for i, line in enumerate(lines):
        if not _NUM_ROW.match(line) or i + 1 >= len(lines):
            continue
        if not _LETTER_ROW.match(lines[i + 1]):
            continue
        numbers, verdicts = line.split(), lines[i + 1].split()
        if len(numbers) != len(verdicts):
            raise GridError(
                f"misaligned band: {len(numbers)} numbers vs {len(verdicts)} verdicts"
            )
        for n, v in zip(numbers, verdicts, strict=True):
            token = v.upper()
            grid[int(n)] = None if token in ANNULMENT_TOKENS else token
    if not grid:
        raise GridError("no number/letter bands found in the scoped section")
    numbers_found = sorted(grid)
    if numbers_found != list(range(1, len(numbers_found) + 1)):
        raise GridError(
            f"recovered item numbers are not 1..{len(numbers_found)} "
            f"({numbers_found[0]}..{numbers_found[-1]}) — the section is probably mis-scoped"
        )
    return grid


# -------------------------------------------------------------- the keyword gate ---

# Seed list from the design doc §5.5, verbatim. Step 4 measures its recall against a
# hand-checked exam; anything added lives in KEYWORDS_ADDED so the delta stays visible.
KEYWORDS_SEED = [
    "CPP",
    "processo penal",
    "inquérito",
    "flagrante",
    "preventiva",
    "temporária",
    "audiência de custódia",
    "denúncia",
    "queixa-crime",
    "resposta à acusação",
    "absolvição sumária",
    "nulidade",
    "prova ilícita",
    "interceptação",
    "habeas corpus",
    "júri",
    "pronúncia",
    "apelação",
    "recurso em sentido estrito",
    "competência",
    "Ministério Público",
    "delegado",
    "réu",
    "acusado",
    "sentença penal",
    "execução penal",
]

# Everything below was added by Step 4, after hand-checking the 43º Exame. The seed
# list is almost entirely procedural, so it kept 5 of the 12 core criminal items and
# missed every question of *penal material* — 57 to 62 and 67 scored zero seed hits
# between them. These are the words that close that gap. Nothing was removed.
KEYWORDS_ADDED = [
    "código penal",
    "crime",
    "criminal",
    "delito",
    "penal",
    "pena",
    "dosimetria",
    "prescrição da pretensão punitiva",
    "tipicidade",
    "dolo",
    "culposo",
    "legítima defesa",
    "furto",
    "roubo",
    "homicídio",
    "estelionato",
    "tráfico",
    "lei de drogas",
    "condenação",
    "condenado",
    "absolvição",
    "prisão",
    "delegacia",
    "autoridade policial",
    "vítima",
    "ofendido",
    "querelante",
    "querelado",
    "indiciado",
    "investigado",
    "defensor",
    "defensoria pública",
    "juizado especial criminal",
    "transação penal",
    "suspensão condicional",
    "acordo de não persecução",
    "boletim de ocorrência",
    "reincidente",
    "regime fechado",
    "regime semiaberto",
    "regime aberto",
    "livramento condicional",
    "delação",
    "colaboração premiada",
    "busca e apreensão",
    "mandado de prisão",
    "testemunha",
    "interrogatório",
    "instrução criminal",
    "trânsito em julgado",
    "recurso especial",
    "revisão criminal",
    "maria da penha",
    "violência doméstica",
    "improbidade",
    "domicílio",
    "inviolabilidade",
    "busca domiciliar",
    "mandado judicial",
    "ilícito",
    "polícia",
    "policial",
    "tipo penal",
    "culpabilidade",
    "imputável",
    "antijurídico",
    "tribunal do júri",
    "delegacia",
]

MIN_KEYWORD_HITS = 2

# How much inflection a keyword may absorb. Plain substring matching is wrong here and
# the sweep measures why: `júri` folds to `juri` and then fires on *jurídico*,
# *jurisprudência* and *jurisdição*, and `pena` fires on *apenas* — between them they
# kept 10 items of pure civil and labour law on the 43º. Anchoring the match at a word
# start and allowing at most three trailing letters keeps the inflections that matter
# (crime/crimes, dolo/doloso, réu/réus) and drops both pathologies.
_SUFFIX_SLACK = 3


def _fold(text: str) -> str:
    """Casefold and strip diacritics so `inquérito` matches `INQUERITO`."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(_fold(keyword))}\w{{0,{_SUFFIX_SLACK}}}(?!\w)")


def keyword_hits(item: objetiva.ObjetivaItem, keywords: list[str]) -> set[str]:
    haystack = _fold(item.stem + "\n" + "\n".join(c["text"] for c in item.choices))
    return {k for k in keywords if _pattern(k).search(haystack)}


def kept(items: list[objetiva.ObjetivaItem], keywords: list[str]) -> list[int]:
    return [
        int(i.number) for i in items if len(keyword_hits(i, keywords)) >= MIN_KEYWORD_HITS
    ]


# Step 4's hand check, read off the 43º Exame (exam id 16773) question by question.
# 57-68 is the exam's own penal/processo-penal block. The four borderline items sit in
# other blocks but turn on material this course teaches, so a domain reader would want
# them offered: 4 (medidas assecuratórias in a criminal prosecution), 6 (excesso de
# prazo na preventiva, habeas corpus by a non-lawyer), 7 (prerrogativas do advogado no
# inquérito, direitos do preso em flagrante), 15 (inviolabilidade do domicílio).
HAND_CHECKED_EXAM = "16773"
HAND_CHECKED_CORE = [57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68]
HAND_CHECKED_BORDERLINE = [4, 6, 7, 15]


# ------------------------------------------------------------------ sweep ---


@dataclass
class Row:
    exam_id: str
    year: int | None
    caderno: bool
    gabarito: bool
    key_kind: str = "-"
    doc_health: str = "-"
    anchor: str = "-"
    marker: str = "-"
    items: int = 0
    usable: int = 0
    unhealthy: int = 0
    punctuated: int = 0
    contiguous: bool = False
    grid: int = 0
    matched: int = 0
    annulled: int = 0
    kept_seed: int = 0
    kept_tuned: int = 0
    note: str = ""
    annulled_numbers: list[int] = field(default_factory=list)
    heading: str = "-"

    @property
    def clean(self) -> bool:
        """All 80 items recovered, healthy, and covered by an 80-entry grid.

        Deliberately *not* gated on whole-document `text_health`: two exams carry an
        unmapped cover page and 80 perfectly legible questions, so judging the
        document as a whole would discard them (see E11).
        """
        return self.ingestible and self.items == 80 and self.contiguous

    @property
    def ingestible(self) -> bool:
        """Weaker, and the one the milestone actually needs: every item that *was*
        recovered is legible and has an answer in a full 80-entry grid.

        An exam that loses one item to a typesetting glitch still yields 79 attributable
        questions; there is no reason to discard it.
        """
        return (
            self.items >= 76
            and self.usable == self.items
            and self.unhealthy == 0
            and self.grid == 80
            and self.matched == self.items
        )


def _marker_profile(text: str) -> str:
    paren = len(re.findall(r"^[ \t]*\([A-E]\)", text, re.M))
    bare = len(re.findall(r"^[ \t]*[A-E]\)", text, re.M))
    if paren and bare:
        return f"mixed {paren}/{bare}"
    return f"(A) x{paren}" if paren else f"A) x{bare}"


def sweep_exam(exam_id: str, art: Artifacts, fetcher: Fetcher, keywords: list[str]) -> Row:
    year = art.caderno.date.year if art.caderno and art.caderno.date else None
    row = Row(
        exam_id=exam_id,
        year=year,
        caderno=art.caderno is not None,
        gabarito=art.gabarito is not None,
        key_kind="definitivo" if art.definitivo else ("preliminar" if art.gabarito else "-"),
    )
    if art.caderno is None:
        row.note = "no Tipo 1 caderno"
        return row

    try:
        caderno = fetcher.get(art.caderno.href).body
    except FetchError as exc:
        row.note = f"caderno fetch failed: {exc}"
        return row

    text = extract_columns(caderno, columns=2)
    row.doc_health = text_health(text)
    row.marker = _marker_profile(text)

    row.anchor, items = best_anchor(text)
    row.items = len(items)
    row.usable = sum(1 for i in items if i.usable)
    row.unhealthy = sum(1 for i in items if text_health(_item_text(i)) != "ok")
    # The shipped default anchor+marker, unchanged, on the same text. E4 claims 0
    # items for the OAB under it. (There is no separate "marker" axis to measure
    # any more — `_CHOICE`'s opening parenthesis is unconditionally optional in
    # the shipped module, so a `paren`-only column would just duplicate `items`.)
    row.punctuated = len(objetiva.segment_objetiva(text))
    numbers = [int(i.number) for i in items]
    row.contiguous = bool(numbers) and numbers == list(range(1, len(numbers) + 1))

    row.kept_seed = len(kept(items, KEYWORDS_SEED))
    row.kept_tuned = len(kept(items, keywords))

    if art.gabarito is None:
        row.note = "no 1ª-fase gabarito"
        return row
    try:
        gab = fetcher.get(art.gabarito.href).body
    except FetchError as exc:
        row.note = f"gabarito fetch failed: {exc}"
        return row

    gab_text = extract_columns(gab, columns=1)
    if text_health(gab_text) != "ok":
        row.note = f"gabarito {text_health(gab_text)}"
        return row
    head = _TIPO_HEAD.search(gab_text)
    row.heading = " ".join(head.group(0).split()) if head else "absent"
    try:
        grid = read_banded(gab_text, tipo=TIPO)
    except GridError as exc:
        row.note = f"grid: {exc}"
        return row

    row.grid = len(grid)
    row.annulled_numbers = sorted(n for n, v in grid.items() if v is None)
    row.annulled = len(row.annulled_numbers)
    row.matched = sum(1 for n in numbers if n in grid)
    if row.matched != row.items:
        row.note = f"{row.items - row.matched} of {row.items} items absent from the grid"
    return row


def _item_text(item: objetiva.ObjetivaItem) -> str:
    return item.stem + " " + " ".join(c["text"] for c in item.choices)


# --------------------------------------------------- cross-source controls ---

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "cebraspe"


def cross_source() -> list[str]:
    """What a `bare`/`questao` anchor does to the two sources that parse correctly
    under the shipped `punctuated` default.

    Task 2 pins these numbers in `tests/test_objetiva.py` so a future merge of the
    `bare`/`questao` patterns into `punctuated` cannot silently re-cut MPRS and
    MPF. There is no separate "tolerant marker" axis to measure any more —
    `_CHOICE`'s opening parenthesis is unconditionally optional in the shipped
    module, so the `punctuated` figure below already reflects it.
    """
    lines: list[str] = []
    for name in ("mprs_50", "mpf_31"):
        path = FIXTURES / f"{name}.txt"
        if not path.exists():
            lines.append(f"{name}: fixture missing")
            continue
        text = path.read_text(encoding="utf-8")
        shipped = objetiva.segment_objetiva(text, item_style="punctuated")
        bare = objetiva.segment_objetiva(text, item_style="bare")
        questao = objetiva.segment_objetiva(text, item_style="questao")
        b_nums = [int(i.number) for i in bare]
        contiguous = bool(b_nums) and b_nums == list(range(b_nums[0], b_nums[0] + len(b_nums)))
        lines.append(
            f"{name}: punctuated -> {len(shipped)} items | "
            f"bare -> {len(bare)} items, "
            f"{'contiguous' if contiguous else 'not contiguous'}"
            + (f" ({b_nums[0]}..{b_nums[-1]})" if b_nums else "")
            + f" | questao -> {len(questao)} items"
        )
    return lines


class ChainedFetcher:
    """Try each `Fetcher` in turn. Every one of them is still a `harvest.http.Fetcher`.

    The 43º Exame's two PDFs were downloaded during the design probe into their own
    cache directory; reading them from there rather than re-fetching keeps this sweep
    at 36 downloads and honours the cache rule harvest etiquette is built around.
    """

    def __init__(self, *fetchers: Fetcher) -> None:
        self._fetchers = fetchers

    def get(self, url: str):
        for i, fetcher in enumerate(self._fetchers):
            try:
                return fetcher.get(url)
            except FetchError:
                if i == len(self._fetchers) - 1:
                    raise
        raise FetchError(f"{url}: no fetcher configured")


# ------------------------------------------------------------------- main ---


def _table(rows: list[Row]) -> str:
    header = (
        "exam    year cad gab key         doc-health      anchor   marker        "
        "items usable ill punct ctg grid match ann seed tuned note"
    )
    out = [header, "-" * len(header)]
    for r in rows:
        out.append(
            f"{r.exam_id:<7} {r.year or '-':<4} "
            f"{'Y' if r.caderno else 'n':<3} {'Y' if r.gabarito else 'n':<3} "
            f"{r.key_kind:<11} {r.doc_health:<15} {r.anchor:<8} {r.marker:<13} "
            f"{r.items:<5} {r.usable:<6} {r.unhealthy:<3} {r.punctuated:<5} "
            f"{'Y' if r.contiguous else 'n':<3} "
            f"{r.grid:<4} {r.matched:<5} {r.annulled:<3} "
            f"{r.kept_seed:<4} {r.kept_tuned:<5} {r.note}"
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="sweep only the first N exams")
    ap.add_argument("--offline", action="store_true", help="fail instead of fetching a PDF")
    ap.add_argument("--json", type=Path, default=None, help="also write the rows as JSON")
    args = ap.parse_args()

    settings = load_settings()
    index_fetcher = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "oab",
        offline=True,          # the 47 index pages are already cached, from M2
    )
    # The 43º Exame's two PDFs were fetched during the design probe and live in their
    # own cache directory. Reading them from there keeps this sweep at 36 downloads.
    probe = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "probe_1f",
        offline=True,
    )
    live = Fetcher(
        user_agent=settings.harvest.user_agent,
        cache_dir=settings.raw_dir / "oab_1f",
        min_interval=1.5,
        offline=args.offline,
    )

    pdf_fetcher = ChainedFetcher(probe, live)

    print(f"discovering exams from {settings.raw_dir / 'oab'} (offline)...")
    found = discover(index_fetcher)
    in_scope = [
        (eid, art)
        for eid, art in found
        if art.caderno and art.caderno.date and art.caderno.date.year >= MIN_YEAR
    ]
    in_scope.sort(key=lambda p: (p[1].caderno.date, p[0]))
    print(
        f"{len(found)} exams publish a Tipo {TIPO} caderno; "
        f"{len(in_scope)} are {MIN_YEAR} or later; "
        f"{sum(1 for _, a in in_scope if a.gabarito)} of those also publish a 1ª-fase gabarito"
    )
    if args.limit:
        in_scope = in_scope[: args.limit]

    keywords = KEYWORDS_SEED + KEYWORDS_ADDED
    rows = [sweep_exam(eid, art, pdf_fetcher, keywords) for eid, art in in_scope]

    print()
    print(_table(rows))
    print()
    clean = [r for r in rows if r.clean]
    keep = [r for r in rows if r.ingestible]
    print(f"clean (80/80) exams:  {len(clean)}/{len(rows)}  ({', '.join(r.exam_id for r in clean)})")
    print(f"ingestible exams:     {len(keep)}/{len(rows)}  ({', '.join(r.exam_id for r in keep)})")
    for r in rows:
        if not r.ingestible:
            print(
                f"  EXCLUDED {r.exam_id} ({r.year}): "
                f"{r.note or f'only {r.items}/80 items recovered, {r.unhealthy} illegible'}"
            )
    print(f"items recovered: {sum(r.items for r in keep)} across {len(keep)} exams")
    print(f"annulled items, total: {sum(r.annulled for r in keep)} (ingestible exams only)")
    print(
        f"kept by the seed gate: {sum(r.kept_seed for r in keep)}; "
        f"by the tuned gate: {sum(r.kept_tuned for r in keep)}  (ingestible exams only)"
    )
    for r in rows:
        if r.annulled_numbers:
            print(f"  {r.exam_id} annulled at {r.annulled_numbers}")

    print()
    print("conventions observed:")
    print(f"  anchors:  {dict(Counter(r.anchor for r in rows))}")
    print(f"  markers:  {dict(Counter(r.marker.split()[0] for r in rows))}")
    print(f"  headings: {sorted({re.sub(r'^[^ ]+ ', '', r.heading) for r in rows})}")
    print(f"  whole-document health: {dict(Counter(r.doc_health for r in rows))}")

    print()
    hand = [r for r in rows if r.exam_id == HAND_CHECKED_EXAM]
    if hand:
        art = dict(in_scope)[HAND_CHECKED_EXAM]
        text = extract_columns(pdf_fetcher.get(art.caderno.href).body, columns=2)
        _, items = best_anchor(text)
        wanted = HAND_CHECKED_CORE + HAND_CHECKED_BORDERLINE
        print(f"keyword-gate recall, hand-checked on exam {HAND_CHECKED_EXAM}:")
        for label, kws in (("seed", KEYWORDS_SEED), ("tuned", keywords)):
            got = set(kept(items, kws))
            core = [n for n in HAND_CHECKED_CORE if n in got]
            all_ = [n for n in wanted if n in got]
            print(
                f"  {label:<6} keeps {len(got):>2}/80 | "
                f"core {len(core)}/{len(HAND_CHECKED_CORE)} | "
                f"core+borderline {len(all_)}/{len(wanted)} | "
                f"missed {[n for n in wanted if n not in got]}"
            )

    print()
    print("cross-source controls (Task 2 pins these):")
    for line in cross_source():
        print(f"  {line}")

    if args.json:
        args.json.write_text(
            json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
