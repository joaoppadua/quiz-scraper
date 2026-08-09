"""Cebraspe combined caderno: item, answer and the banca's justificativa in one file.

Two certames publish this genre, and it is M3's headline artifact — the same
self-contained shape that made M2 cheap. The layout is:

    Acerca da prova no processo penal, julgue os itens a seguir.   <- comando
    87 O afastamento da prova pericial não enseja nulidade...      <- item
    JUSTIFICATIVA - Errado. O princípio do livre convencimento...  <- key + rationale
    <FimJust>                                                      <- 2026 only

Each item is governed by "o comando que imediatamente o antecede", in the caderno's
own words, and a comando typically governs three to seven siblings.

Two things are deliberately not relied upon. The `<FimJust>` sentinel appears in the
2026 caderno and not in the 2019 one, so segmentation keys on the item number and the
JUSTIFICATIVA line instead. And the comando is stored separately rather than prefixed
to the stem, because a real comando outruns `models.stem_hash`'s window and would
make every item in a block hash identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The professor's scope decision: accept items whose comando is a topic sentence,
# reject those hanging off a multi-paragraph fact pattern, because the latter read as
# fragments when lifted alone to open a class.
#
# Length alone is a poor test — the julgue sentence of a fact-pattern block ("Com base
# nessa situação hipotética, julgue os itens a seguir") is itself short. What actually
# distinguishes them is that such a comando *refers back* to narrative the item cannot
# stand without. Cebraspe phrases that reference with a small stock vocabulary.
MAX_COMANDO_CHARS = 250

_REFERS_BACK = re.compile(
    r"situa[çc][ãa]o\s+hipot[ée]tica"
    r"|caso\s+hipot[ée]tico"
    r"|situa[çc][ãa]o\s+apresentada"
    r"|texto\s+(?:precedente|anterior|acima|apresentado)"
    r"|(?:com\s+base|a\s+partir|tendo\s+(?:em\s+vista|como\s+refer[êe]ncia))\s+(?:n?[oa]s?\s+)?"
    r"(?:situa[çc][ãa]o|caso|texto|fragmento|informa[çc][õo]es\s+precedentes)"
    r"|figura\s+(?:precedente|anterior)"
    r"|dados\s+(?:precedentes|apresentados)",
    re.I,
)

# An item begins at a line start with its number, followed by the proposition.
_ITEM = re.compile(r"^[ \t]*(\d{1,3})[ \t]+(?=\S)", re.M)
# The verdict and the rationale arrive on one line.
_JUST = re.compile(r"JUSTIFICATIVA\s*[-–—]\s*(CERTO|ERRADO|ANULAD\w*)\s*\.?", re.I)
# A comando ends with its "julgue ..." instruction.
# The julgue sentence, which is the comando proper. Newline-tolerant because it wraps
# across lines in the extracted text, but stopping at the previous sentence's period.
_JULGUE = re.compile(r"[^.]*\bjulgue\b[^.]*\.", re.I)
_SENTINEL = re.compile(r"<\s*FimJust\s*>", re.I)

_FURNITURE = re.compile(
    r"^\s*(?:"
    r"CEBRASPE\s*[–—-].*"
    r"|--\s*PROVA\s+OBJETIVA\s*--.*"
    r"|.*Espa[çc]o\s+livre.*"
    r"|.*Edital:\s*\d.*"
    r"|P[áa]gina\s+\d+.*"
    r")\s*$",
    re.I | re.M,
)

_VERDICT = {"CERTO": "C", "ERRADO": "E"}


@dataclass(frozen=True)
class CadernoItem:
    number: str
    comando: str | None
    stem: str
    answer_key: str | None       # "C" | "E" | None when annulled
    rationale: str

    @property
    def usable(self) -> bool:
        """Whether this item can open a class on its own.

        Rejects items whose comando points back to narrative that is not carried with
        the item. The item text is still returned, so the caller logs what it skipped.
        """
        if not self.stem or not self.rationale or self.answer_key is None:
            return False
        if not self.comando:
            return True
        if _REFERS_BACK.search(self.comando):
            return False
        return len(self.comando) <= MAX_COMANDO_CHARS


def segment_caderno(text: str) -> list[CadernoItem]:
    """Split a combined caderno into items. Pure: no I/O.

    One forward pass. The text between the end of one item's rationale and the start
    of the next item is the gap; when a gap contains a "julgue ..." instruction it
    opens a new block and becomes that block's comando, otherwise the item inherits
    the comando already in force. This is the caderno's own rule — "cada um dos itens
    está vinculado ao comando que imediatamente o antecede".
    """
    text = _SENTINEL.sub("\n", text)
    marks = list(_ITEM.finditer(text))
    if not marks:
        return []

    items: list[CadernoItem] = []
    comando: str | None = None
    cursor = 0                      # end of the previous item's rationale

    for i, m in enumerate(marks):
        gap = text[cursor : m.start()]
        julgue = None
        for j in _JULGUE.finditer(gap):
            julgue = j                      # the last one wins: it precedes this item
        if julgue:
            comando = " ".join(_clean(julgue.group(0)).split()) or None

        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end]
        just = _JUST.search(body)
        if not just:
            continue                # a stray number in prose, not an item
        stem = _clean(body[: just.start()])
        rationale = _clean(body[just.end() :])
        if not stem or not rationale:
            continue

        items.append(
            CadernoItem(
                number=str(int(m.group(1))),
                comando=comando,
                stem=stem,
                answer_key=_VERDICT.get(just.group(1).upper()),
                rationale=rationale,
            )
        )
        cursor = m.end() + just.end()

    return items


def _clean(block: str) -> str:
    return "\n".join(
        line for line in _FURNITURE.sub("", block).splitlines() if line.strip()
    ).strip()
