"""A-E prova reader and the answer-grid conventions (spec §13, amended).

Spec §13 describes one grid shape — "grids of item→letter; X = nullified" — and one
annulment token. Neither generalises. Two distinct layouts occur across the seed set:

  paired_rows (Cebraspe)   an `Item` line of numbers and a `Gabarito` line of letters
                           directly beneath it, aligned by column. Parsing line by
                           line finds nothing at all; the rows must be zipped.

  interleaved (MPRS, MPF)  several `number answer` cells on one physical line:
                           `1 ANULADA   26 A   51 C   76 E`

  banded (OAB 1a-fase)     a row of bare item numbers, then a row of the same many
                           letters directly beneath it — `1 2 3 ... 20` over
                           `C A A C ...` — repeated once per "tipo": one gabarito
                           file carries all four tipos of one exam back to back.

and annulment is written five different ways: `X`, `ANULADA`, `N`, `*` (the OAB's),
and a `-` in the justificativa tables. So `read_grid` refuses to guess: a grid
whose convention it cannot positively identify raises rather than defaulting to
"nothing annulled", because that default silently ships wrong answer keys.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Annulment tokens observed across the seed set. A value of None in a parsed grid
# means "annulled", never "missing".
ANNULMENT_TOKENS: frozenset[str] = frozenset({"X", "ANULADA", "ANULADO", "N", "*"})

# The item anchor is selectable per source (amendment E13): a caderno numbers its
# questions one of three ways, and none of the three may be widened to also accept
# the others — doing so collapses a punctuated source's real item count (50 for
# MPRS, 31 for MPF) to a handful of unrelated single-digit candidates (measured in
# the M2.5 Task 1 recon; pinned in tests/test_objetiva.py).
_ITEM_STYLES: dict[str, re.Pattern] = {
    # "12. Enunciado" / "12) Enunciado" — MPRS, MPF, and every M3 source.
    "punctuated": re.compile(r"^[ \t]*(\d{1,3})[ \t]*[\.\)][ \t]+(?=\S)", re.M),
    # A bare numeral alone on its own line — how most OAB 1ª-fase cadernos number.
    "bare": re.compile(r"^[ \t]*(\d{1,3})[ \t]*$", re.M),
    # "Questão 12" alone on its own line — exams 11561/11562 (XXVIII/XXIX) instead.
    "questao": re.compile(r"^[ \t]*Quest[aã]o[ \t]+(\d{1,3})[ \t]*$", re.M | re.I),
}
# Choices are letters at a line start, closing paren mandatory; roman sub-items
# (I-, II-) are not. The opening parenthesis is optional (amendment E12): three OAB
# exams write "A)" with no parenthesis at all, and one mixes "(A)" and "A)" inside a
# single caderno, so this cannot be a per-source switch like the item anchor above —
# it must be one globally tolerant pattern. Measured safe: with the opening
# parenthesis optional, MPRS still segments to 50 items and MPF to 31, unchanged.
# MPRS writes labels uppercase, MPF lowercase — labels are normalised to uppercase.
_CHOICE = re.compile(r"^[ \t]*\(?([A-Ea-e])\)[ \t]*", re.M)

_PAIR_ITEM = re.compile(r"^\s*Item\b(.*)$", re.I)
_PAIR_GAB = re.compile(r"^\s*Gabarito\b(.*)$", re.I)
# One "number answer" cell. Deliberately case-sensitive and space-separated:
# a lowercase "e"/"a" is Portuguese, not an answer, and a cell never straddles a
# newline. ANULADA comes first so "1 ANULADA" cannot split as ("1", "A").
_CELL = re.compile(r"(\d{1,3})[ \t]+(ANULAD[AO]|[A-EN]|X)(?![A-Za-zÀ-ÿ])")
# A grid row is nothing but cells. Prose lines that merely contain a cell-shaped
# fragment ("As questões 1 a 14", "(C) Apenas 3 e 4", a margin line number) are not.
_GRID_ROW = re.compile(r"^[ \t]*(?:\d{1,3}[ \t]+(?:ANULAD[AO]|[A-EN]|X)(?![A-Za-zÀ-ÿ])[ \t]*)+$")

# The OAB's "tipo" heading has four spellings across its 19 gabaritos (amendment
# E14, measured in the M2.5 recon sweep):
#   "PROVA TIPO 1"                                       (44º)
#   "43º EXAME DE ORDEM - PROVA TIPO 1"                  (42º onward)
#   "XXVIII EXAME DE ORDEM UNIFICADO – TIPO 1 – BRANCO"  (2019 .. 2023-03, en-dashes)
#   "XXXIX EXAME DE ORDEM UNIFICADO - PROVA 1"           (39º, no "TIPO" token at all)
# Nothing is common to all four but the tipo token itself, so the pattern binds on
# that alone and rejects prose by requiring a short line. This still matches more
# than four times per file (fix-round-1, Important finding A of the task-3
# review): the correspondence table's column header ("TIPO 1 TIPO 2 TIPO 3 TIPO 4
# TIPO 1 TIPO 2 TIPO 3 TIPO 4") is short enough to match too, and — more subtly —
# so can an unrelated short line that merely *mentions* a tipo in passing (a
# retificado note, an errata). `_block_headings` below is what tells the two
# apart: a match only counts as a scope boundary if it genuinely begins a new
# block.
_TIPO_HEAD = re.compile(
    r"^[ \t]*(?=[^\n]{1,60}$)[^\n]*?\b(?:PROVA[ \t]+TIPO|TIPO|PROVA)[ \t]+0?([1-9])\b[^\n]*$",
    re.M | re.I,
)

# A band's head row: five or more bare item numbers on their own line. Its answer
# row sits directly beneath, drawn from the same alphabet `_verdict` already
# knows — the OAB always writes annulment "*", already in ANNULMENT_TOKENS, so
# no new constant is needed. Requiring the answer row to look like verdict tokens
# (not more bare digits) is defence-in-depth against a table-shaped pair of
# integer-only rows landing inside a tipo's scope and being read as answers —
# see `test_band_answer_row_guard_rejects_a_table_shaped_row_within_scope`.
_BAND_HEAD_ROW = re.compile(r"^[ \t]*\d{1,3}(?:[ \t]+\d{1,3}){4,}[ \t]*$")
_BAND_TOKEN = r"(?:ANULAD[AO]|[A-EXN*])"
_BAND_ANSWER_ROW = re.compile(rf"^[ \t]*{_BAND_TOKEN}(?:[ \t]+{_BAND_TOKEN})*[ \t]*$")


# Running heads and page numbers, which sit between questions in the extracted text
# and otherwise land inside a stem or an alternative.
_FURNITURE = re.compile(
    r"^\s*(?:"
    r"P[áa]gina\s+\d+.*"
    r"|\d+\s*/\s*\d+"
    r"|MINIST[ÉE]RIO\s+P[ÚU]BLICO.*"
    r"|SECRETARIA\s+DE\s+CONCURSOS.*"
    r"|\d+[°º]\s+CONCURSO.*"
    r"|PROVA\s+OBJETIVA\s*$"
    r")\s*$",
    re.I | re.M,
)


def _clean(block: str, extra: re.Pattern | None = None) -> str:
    text = _FURNITURE.sub("", block)
    if extra is not None:
        text = extra.sub("", text)
    return "\n".join(line for line in text.splitlines() if line.strip()).strip()


class GridError(ValueError):
    """The answer grid could not be read with confidence."""


@dataclass(frozen=True)
class ObjetivaItem:
    number: str
    stem: str
    choices: list[dict[str, str]] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.stem.strip()) and len(self.choices) >= 4


# A prova is numbered sequentially, and stems enumerate their own affirmations the
# same way ("6. Considere as seguintes afirmações." followed by "1.", "2.", "3.").
# Only a candidate that continues the question sequence is a question; a small
# forward jump is allowed so one unparsed question does not desynchronise the rest.
_RESYNC_WINDOW = 5


def _run_from(candidates: list[re.Match], start: int) -> list[re.Match]:
    run = [candidates[start]]
    expected = int(candidates[start].group(1)) + 1
    for m in candidates[start + 1 :]:
        n = int(m.group(1))
        if expected <= n <= expected + _RESYNC_WINDOW:
            run.append(m)
            expected = n + 1
    return run


def _question_marks(text: str, pattern: re.Pattern) -> list[re.Match]:
    """The longest sequential run of candidates.

    Taking the first candidate is not safe: a prova's cover page carries its own
    enumerated instructions ("1. NÃO HAVERÁ SUBSTITUIÇÃO...", "a) Reveja as
    questões"), and anchoring there swallows the first real questions into one
    oversized item. The same arbiter also resolves the OAB caderno's trailing
    questionário de percepção, which restarts its own 1..10 numbering: the 20-item
    prova run outlasts the questionário's run of 10.

    The real sequence is the longest run, and an equal-length tie breaks toward the
    *later* start, because a cover page always precedes the questions it introduces.
    """
    candidates = list(pattern.finditer(text))
    if not candidates:
        return []
    best: list[re.Match] = []
    for i in range(len(candidates)):
        if len(candidates) - i < len(best):
            break                        # no later start can match the current best
        run = _run_from(candidates, i)
        if len(run) >= len(best):
            best = run
    return best


def _tail_boundary(text: str, pattern: re.Pattern, after: int) -> re.Match | None:
    """The first later candidate under `pattern` that itself looks like a genuine
    item start — a stem followed by at least four choice-shaped lines before the
    *next* candidate under the same pattern (or end of text), the same floor
    `segment_objetiva` applies everywhere else before calling something a
    question.

    Not-part-of-the-winning-run is not the same as marks-a-new-section: a later
    candidate merely failed the sequential-continuation test in `_question_marks`,
    which says nothing about whether it sits inside the true last item's own body
    (an incidental digit alone on a line) or genuinely starts a new, unrelated
    sequence (the OAB caderno's trailing questionário de percepção). Only the
    latter should ever bound the last item's body.
    """
    candidates = [m for m in pattern.finditer(text) if m.start() >= after]
    for i, m in enumerate(candidates):
        end = candidates[i + 1].start() if i + 1 < len(candidates) else len(text)
        if len(list(_CHOICE.finditer(text[m.end() : end]))) >= 4:
            return m
    return None


def segment_objetiva(
    text: str, *, furniture: list[str] | None = None, item_style: str = "punctuated"
) -> list[ObjetivaItem]:
    """Split an A-E prova into numbered questions with their alternatives.

    `furniture` adds per-source running heads to strip, so adding a concurso whose
    header differs stays a manifest edit rather than a code change.

    `item_style` selects which item anchor to use (see `_ITEM_STYLES`). An unknown
    style raises rather than silently falling back — a wrong parse ships wrong
    answers to students.
    """
    try:
        pattern = _ITEM_STYLES[item_style]
    except KeyError:
        raise ValueError(
            f"unknown item_style {item_style!r}; expected one of {sorted(_ITEM_STYLES)}"
        ) from None
    marks = _question_marks(text, pattern)
    if not marks:
        return []
    extra = re.compile("|".join(f"^.*{f}.*$" for f in furniture), re.I | re.M) if furniture else None

    # A single-file prova carries its answer grid after the last question; without
    # this bound the final alternative swallows the whole grid and renders it to the
    # professor above the spoiler block.
    limit = len(text)
    for line_match in re.finditer(r"^.*$", text[marks[-1].end() :], re.M):
        if _GRID_ROW.match(line_match.group(0)):
            limit = marks[-1].end() + line_match.start()
            break

    # A later candidate that itself looks like a genuine item (see
    # `_tail_boundary`) marks where a new, unrelated section begins, and the
    # winning run's last item must not reach past it: the OAB caderno's trailing
    # *questionário de percepção* restarts at 1 (E16), and once `_CHOICE`
    # tolerates a bare "A)" (E12) its own alternatives would otherwise read as
    # more choices tacked onto the prova's last item. `bare` is checked in
    # addition to whichever style was selected, but *only* for `bare`/`questao` —
    # this is an OAB-specific fact (the questionário is always numbered with a
    # bare numeral, even under a `questao` anchor such as 11561/11562) with no
    # bearing on punctuated sources. Running it unconditionally, tried in an
    # earlier round, let an incidental bare digit inside a real MPRS/MPF item's
    # own body masquerade as a section boundary and silently dropped the item
    # (caught by task review). Verified across all 19 in-scope exams
    # (recon_1f.py, offline): this closes the gap without moving the
    # `punctuated`-style MPRS/MPF regression, since their own trailing
    # interleaved grid is always bounded first by `_GRID_ROW`.
    tail_patterns = {pattern}
    if item_style in ("bare", "questao"):
        tail_patterns.add(_ITEM_STYLES["bare"])
    for tail_pattern in tail_patterns:
        tail = _tail_boundary(text, tail_pattern, marks[-1].end())
        if tail is not None:
            limit = min(limit, tail.start())
    text = text[:limit]

    items: list[ObjetivaItem] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end]
        choices = list(_CHOICE.finditer(body))
        if len(choices) < 4:
            continue                     # a numbered line in prose, not a question
        stem = _clean(body[: choices[0].start()], extra)
        parsed: list[dict[str, str]] = []
        for j, c in enumerate(choices):
            tail = choices[j + 1].start() if j + 1 < len(choices) else len(body)
            parsed.append(
                {"label": c.group(1).upper(), "text": _clean(body[c.end() : tail], extra)}
            )
        if not stem:
            continue
        items.append(ObjetivaItem(number=str(int(m.group(1))), stem=stem, choices=parsed))
    return items


def read_grid(text: str, *, style: str, tipo: int | None = None) -> dict[int, str | None]:
    """Answer grid as {item number: letter}, with None for an annulled item.

    `tipo` is required (and only meaningful) for `style="banded"` — the OAB's
    gabarito carries all four tipos of one exam in a single file, and a caller
    must say which one it wants rather than getting whichever the parser scans
    last.
    """
    if style == "paired_rows":
        return _read_paired_rows(text)
    if style == "interleaved":
        return _read_interleaved(text)
    if style == "banded":
        return _read_banded(text, tipo)
    raise GridError(f"unknown grid style {style!r}")


def _read_paired_rows(text: str) -> dict[int, str | None]:
    lines = text.splitlines()
    grid: dict[int, str | None] = {}
    for i, line in enumerate(lines):
        m = _PAIR_ITEM.match(line)
        if not m or i + 1 >= len(lines):
            continue
        g = _PAIR_GAB.match(lines[i + 1])
        if not g:
            continue
        numbers = re.findall(r"\d{1,3}", m.group(1))
        verdicts = re.findall(r"\b([A-E]|X)\b", g.group(1), re.I)
        if len(numbers) != len(verdicts):
            raise GridError(
                f"misaligned grid rows: {len(numbers)} items vs {len(verdicts)} verdicts"
            )
        for n, v in zip(numbers, verdicts, strict=True):
            grid[int(n)] = _verdict(v)
    if not grid:
        raise GridError("no Item/Gabarito row pairs found")
    return grid


def _read_interleaved(text: str) -> dict[int, str | None]:
    """Cells, taken only from lines that consist entirely of cells.

    MPRS ships the prova and its grid in one file, so this is handed 43 pages of
    prose in which "number letter" pairs are everywhere. Accepting them produced six
    wrong answer keys and un-annulled an officially annulled question — the worst
    outcome this corpus can have, since a wrong key reaches a student as fact.
    """
    grid: dict[int, str | None] = {}
    for line in text.splitlines():
        if not _GRID_ROW.match(line):
            continue
        for n, v in _CELL.findall(line):
            grid[int(n)] = _verdict(v)        # a later row supersedes an earlier one
    if not grid:
        raise GridError("no grid rows found (a grid row contains nothing but cells)")
    _check_contiguous(grid)
    return grid


def _next_band_head_tokens(text: str, after: int) -> list[str] | None:
    """The tokens of the first band-head-shaped line at or after `after`, or None
    if the rest of the text has none."""
    for line in text[after:].splitlines():
        if _BAND_HEAD_ROW.match(line):
            return line.split()
    return None


def _block_headings(text: str) -> list[re.Match]:
    """Every `_TIPO_HEAD` match that genuinely begins a new block.

    Fix-round-1, Important finding A of the task-3 review: the ≤60-char guard
    alone lets a short line that merely *mentions* a tipo in passing (a
    retificado note, an errata — the OAB does republish corrected gabaritos)
    silently truncate the requested block, and because truncation drops a
    contiguous *tail* the existing contiguity check does not catch it. The fix
    is not "does this line look heading-shaped" but "does a new block of
    answers actually start here" — and a block always restarts its own item
    numbering at 1. So a match only counts as a boundary if the first
    band-head-shaped row that follows it opens with "1". A stray mention is
    followed by whatever row happens to come next (item 6, 21, ...) and is
    correctly rejected; a genuine tipo heading and the correspondence table's
    own column header (whose body row is "1 4 6 2 ...") both restart at 1 and
    are correctly kept.
    """
    headings = []
    for h in _TIPO_HEAD.finditer(text):
        tokens = _next_band_head_tokens(text, h.end())
        if tokens and tokens[0] == "1":
            headings.append(h)
    return headings


def _read_banded(text: str, tipo: int | None) -> dict[int, str | None]:
    """OAB 1a-fase gabarito: a row of item numbers, then a row of the same many
    letters directly beneath it, repeated once per tipo (all four share one file).

    Scoping to the requested tipo is mandatory, not best-effort: every tipo shares
    the same 1..80 numbering, so an unscoped read lets a later tipo's rows silently
    supersede an earlier tipo's at the exact same item numbers, and ships the wrong
    tipo's answers as fact.
    """
    if not tipo:
        raise GridError("style 'banded' requires a tipo")
    heads = _block_headings(text)
    wanted = [h for h in heads if int(h.group(1)) == tipo]
    if not wanted:
        raise GridError(f"no heading found for tipo {tipo}")
    start = wanted[0].end()          # the first match for the requested tipo
    later = [h for h in heads if h.start() > start]
    end = later[0].start() if later else len(text)   # the next block heading, any tipo
    block = text[start:end]

    lines = block.splitlines()
    grid: dict[int, str | None] = {}
    for i, line in enumerate(lines):
        if not _BAND_HEAD_ROW.match(line) or i + 1 >= len(lines):
            continue
        if not _BAND_ANSWER_ROW.match(lines[i + 1]):
            continue
        numbers = line.split()
        verdicts = lines[i + 1].split()
        if len(numbers) != len(verdicts):
            raise GridError(
                f"misaligned band for tipo {tipo}: "
                f"{len(numbers)} numbers vs {len(verdicts)} verdicts"
            )
        for n, v in zip(numbers, verdicts, strict=True):
            grid[int(n)] = _verdict(v)
    if not grid:
        raise GridError(f"no number/letter bands found for tipo {tipo}")
    _check_contiguous(grid)
    return grid


def _check_contiguous(grid: dict[int, str | None]) -> None:
    numbers = sorted(grid)
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        raise GridError(
            f"recovered item numbers are not contiguous ({numbers[0]}..{numbers[-1]}, "
            f"{len(numbers)} entries) — this is probably not an answer grid"
        )


def _verdict(token: str) -> str | None:
    token = token.strip().upper()
    return None if token in ANNULMENT_TOKENS else token
