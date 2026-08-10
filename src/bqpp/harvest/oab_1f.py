"""Pure selection logic for the OAB 1ª-fase objective exam (M2.5, Task 5).

Two decisions live here, both measured against the cached index pages during recon
(`scripts/recon_1f.py`) rather than assumed:

  select_1f_artifacts   which two PDFs, of the dozens an exam's index page links,
                         are this tipo's caderno and its best available gabarito.

  is_criminal            which of a caderno's 80 questions — spread across roughly
                         14 discipline blocks with no headings to bind on — are
                         criminal-law/criminal-procedure material worth keeping.

Nothing here fetches or writes anything: no network, no DB. `harvest/http.py`
remains the only module that opens a socket. Task 6 wires this into `harvest_source`
and `config/sources.yaml`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bqpp.harvest.oab_site import IndexEntry
from bqpp.parse.objetiva import ObjetivaItem


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
