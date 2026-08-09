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

_ITEM = re.compile(r"^[ \t]*(\d{1,3})[ \t]*[\.\)][ \t]+(?=\S)", re.M)
# Choices are parenthesised letters at a line start; roman sub-items (I-, II-) are not.
# MPRS writes them uppercase, MPF lowercase — labels are normalised to uppercase.
_CHOICE = re.compile(r"^[ \t]*\(([A-Ea-e])\)[ \t]*", re.M)

_PAIR_ITEM = re.compile(r"^\s*Item\b(.*)$", re.I)
_PAIR_GAB = re.compile(r"^\s*Gabarito\b(.*)$", re.I)
_CELL = re.compile(r"\b(\d{1,3})\s+([A-E]|ANULAD[AO]|N|X)\b", re.I)


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


def _question_marks(text: str) -> list[re.Match]:
    """The longest sequential run of candidates.

    Taking the first candidate is not safe: a prova's cover page carries its own
    enumerated instructions ("1. NÃO HAVERÁ SUBSTITUIÇÃO...", "a) Reveja as
    questões"), and anchoring there swallows the first real questions into one
    oversized item.

    The real sequence is the longest run, and an equal-length tie breaks toward the
    *later* start, because a cover page always precedes the questions it introduces.
    """
    candidates = list(_ITEM.finditer(text))
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


def segment_objetiva(text: str) -> list[ObjetivaItem]:
    """Split an A-E prova into numbered questions with their alternatives."""
    marks = _question_marks(text)
    if not marks:
        return []

    items: list[ObjetivaItem] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end]
        choices = list(_CHOICE.finditer(body))
        if len(choices) < 4:
            continue                     # a numbered line in prose, not a question
        stem = body[: choices[0].start()].strip()
        parsed: list[dict[str, str]] = []
        for j, c in enumerate(choices):
            tail = choices[j + 1].start() if j + 1 < len(choices) else len(body)
            parsed.append({"label": c.group(1).upper(), "text": body[c.end() : tail].strip()})
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
    grid: dict[int, str | None] = {}
    for n, v in _CELL.findall(text):
        grid.setdefault(int(n), _verdict(v))
    if not grid:
        raise GridError("no 'number answer' cells found")
    return grid


def _verdict(token: str) -> str | None:
    token = token.strip().upper()
    return None if token in ANNULMENT_TOKENS else token
