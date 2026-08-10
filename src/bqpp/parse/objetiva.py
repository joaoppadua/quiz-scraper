"""A-E prova reader and the answer-grid conventions (spec §13, amended).

Spec §13 describes one grid shape — "grids of item→letter; X = nullified" — and one
annulment token. Neither generalises. Two distinct layouts occur across the seed set:

  paired_rows (Cebraspe)   an `Item` line of numbers and a `Gabarito` line of letters
                           directly beneath it, aligned by column. Parsing line by
                           line finds nothing at all; the rows must be zipped.

  interleaved (MPRS, MPF)  several `number answer` cells on one physical line:
                           `1 ANULADA   26 A   51 C   76 E`

and annulment is written four different ways: `X`, `ANULADA`, `N`, and a `-` in the
justificativa tables. So `read_grid` refuses to guess: a grid whose convention it
cannot positively identify raises rather than defaulting to "nothing annulled",
because that default silently ships wrong answer keys.
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

    # The winning run is the longest *sequential* run of candidates (see
    # `_question_marks`), so by construction no candidate after `marks[-1]` extends
    # it — if one did, it would already be part of the run. A later candidate under
    # the same pattern therefore marks a new, unrelated numbered sequence starting
    # up: the OAB caderno's trailing *questionário de percepção* restarts at 1
    # (E16), and once `_CHOICE` tolerates a bare "A)" (E12) its own alternatives
    # read as more choices tacked onto the prova's last item unless something stops
    # them there. The `bare` pattern is checked in addition to whichever style was
    # selected, because the questionário is always numbered with a bare numeral —
    # even on a caderno anchored on "Questão N" (11561/11562) — so a same-style-only
    # check misses it there. Measured across all 19 in-scope exams (recon_1f.py,
    # offline): this closes the gap for both without moving the `punctuated`-style
    # MPRS/MPF regression, because their own trailing interleaved grid is always
    # bounded first by `_GRID_ROW`, well before any incidental bare-shaped line in
    # their own prose would matter. The earliest candidate under either pattern is
    # where the winning run's last item truly ends, the same way `_GRID_ROW` marks
    # where an inline answer key begins; take whichever bound comes first.
    for tail_pattern in (pattern, _ITEM_STYLES["bare"]):
        tail = next((m for m in tail_pattern.finditer(text) if m.start() >= marks[-1].end()), None)
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


def read_grid(text: str, *, style: str) -> dict[int, str | None]:
    """Answer grid as {item number: letter}, with None for an annulled item."""
    if style == "paired_rows":
        return _read_paired_rows(text)
    if style == "interleaved":
        return _read_interleaved(text)
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
    numbers = sorted(grid)
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        raise GridError(
            f"recovered item numbers are not contiguous ({numbers[0]}..{numbers[-1]}, "
            f"{len(numbers)} entries) — this is probably not an answer grid"
        )
    return grid


def _verdict(token: str) -> str | None:
    token = token.strip().upper()
    return None if token in ANNULMENT_TOKENS else token
