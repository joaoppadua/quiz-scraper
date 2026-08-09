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
# A sentence boundary: a period followed by whitespace and a capital. This is
# deliberately not a character class over "not a period" — an abbreviation-tolerant
# class matches leftmost, so the comando sentence would start back inside the
# previous rationale (after "Min." in a citation such as
# "RE 852.475/SP, red. p/ o ac. Min. Edson Fachin"). Splitting on the boundary and
# taking the last sentence is both simpler and correct.
_BOUNDARY = re.compile(r"(?<=[.:])\s+(?=[A-ZÀ-ÞÁÉÍÓÚÂÊÔÃÕÇ])")
_JULGUE_WORD = re.compile(r"\bjulgue\b", re.I)
_SENTINEL = re.compile(r"<\s*FimJust\s*>", re.I)

_FURNITURE = re.compile(
    r"^\s*(?:"
    r"CEBRASPE\s*[–—-].*"
    # The column crop splits full-width furniture down the middle, so match either half
    r"|.*--\s*PROVA\s+O(?:BJETIVA)?\s*(?:--)?.*"
    r"|.*O?BJETIVA\s*--.*"
    r"|.*Espa[çc]o\s+livre.*"
    r"|.*Edital:\s*\d.*"
    # answer-sheet instruction box, whose right half can look like a comando
    r"|.*Folha\s+de\s+Respostas.*"
    r"|.*campo\s+designado.*"
    r"|.*pontua[çc][ãa]o\s+negativa.*"
    r"|.*preencher\s+o\s+campo.*"
    # the caderno's own cover boilerplate, which otherwise merges into the first comando
    r"|\s*[•·].*"
    r"|.*vinculado\s+ao\s+comando.*"
    r"|.*marque,?\s+na\s+Folha.*"
    r"|.*caderno\s+de\s+(?:prova|rascunho).*"
    r"|.*NÃO\s+HAVERÁ\s+SUBSTITUIÇÃO.*"
    r"|P[áa]gina\s+\d+.*"
    r")\s*$",
    re.I | re.M,
)

# Furniture that survives inside a comando rather than on a line of its own. The
# midpoint column crop cuts full-width headers in half ("Folha de Respostas" becomes
# "Folha de Re"), so line-anchored patterns miss the fragments while the newline-
# tolerant julgue regex spans straight across them. The comando is whatever follows
# the last of these.
_FURNITURE_INLINE = re.compile(
    r"rascunhos?\.?"
    r"|Folha\s+de\s+Re\w*"
    r"|vinculado\s+ao\s+comand\w*"
    r"|Espa[çc]o\s+livre"
    r"|--\s*PROVA\s+O\w*"
    r"|O?BJETIVA\s*--"
    r"|Edital:\s*\d+"
    r"|pontua[çc][ãa]o\s+negativa",
    re.I,
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

    One forward pass. Everything after an item's JUSTIFICATIVA line and before the
    next item belongs to two different things: the rationale, then — sometimes — the
    next block's comando. The boundary between them is the `<FimJust>` sentinel where
    the banca prints one, and otherwise the start of the next "julgue" sentence.
    Getting this wrong absorbs a whole fact pattern into the banca's commentary,
    which is then rendered to the professor under the banca's name.
    """
    marks = list(_ITEM.finditer(text))
    if not marks:
        return []

    items: list[CadernoItem] = []
    comando = _last_julgue(text[: marks[0].start()])

    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end]
        just = _JUST.search(body)
        if not just:
            continue                # a stray number in prose, not an item
        stem = _clean(body[: just.start()])

        tail = body[just.end() :]
        sentinel = _SENTINEL.search(tail)
        if sentinel:
            rationale_raw, after = tail[: sentinel.start()], tail[sentinel.end() :]
        else:
            rationale_raw, after = _split_at_comando(tail)

        rationale = _clean(rationale_raw)
        if stem and rationale:
            items.append(
                CadernoItem(
                    number=str(int(m.group(1))),
                    comando=comando,
                    stem=stem,
                    answer_key=_VERDICT.get(just.group(1).upper()),
                    rationale=rationale,
                )
            )
        # whatever followed the rationale may open the next block
        found = _last_julgue(after)
        if found:
            comando = found

    return items


def _split_at_comando(tail: str) -> tuple[str, str]:
    """Split an item's tail into (rationale, whatever opens the next block).

    The boundary is the start of the sentence carrying the last "julgue" — for the
    2019 caderno, which prints no <FimJust> sentinel, this is the only thing marking
    where the banca's commentary stops and the next comando begins.
    """
    last = None
    for m in _JULGUE_WORD.finditer(tail):
        last = m
    if last is None:
        return tail, ""
    start = 0
    for b in _BOUNDARY.finditer(tail, 0, last.start()):
        start = b.end()
    return tail[:start], tail[start:]


def _last_julgue(chunk: str) -> str | None:
    """The last complete "julgue ..." sentence in a chunk, cleaned of furniture.

    Cleaning happens first: the column crop splits full-width headers, and an
    uncleaned scan can pick the answer-sheet instruction box as a comando.
    """
    cleaned = _clean(chunk)
    if not cleaned:
        return None
    sentences = _BOUNDARY.split(" ".join(cleaned.split()))
    carrying = [s for s in sentences if _JULGUE_WORD.search(s)]
    if not carrying:
        return None
    comando = carrying[-1].strip()
    cut = None
    for f in _FURNITURE_INLINE.finditer(comando):
        cut = f.end()
    if cut is not None:
        comando = comando[cut:].strip(" .;–—-")
    return comando or None


def _clean(block: str) -> str:
    return "\n".join(
        line for line in _FURNITURE.sub("", block).splitlines() if line.strip()
    ).strip()
