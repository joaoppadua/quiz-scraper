"""Segment an OAB *padrão de respostas* into questions (spec §13, amended).

The spec plans to segment the 2ª-fase *caderno* on "QUESTÃO 1..4" and then join a
separate gabarito justificado to it by question number. That join is unnecessary:
the padrão is self-contained. Each section carries the enunciado verbatim *and*
the banca's own reasoning, so one document per exam is the entire artifact.

    PADRÃO DE RESPOSTA – QUESTÃO 1 – B005250     <- section anchor
    Enunciado                                     <- the question, verbatim
    ...
    Gabarito Comentado                            <- the banca's reasoning
    ...
    DISTRIBUIÇÃO DOS PONTOS                       <- itemised scoring

Every anchor here is case-insensitive on purpose. Most exams write the sub-headers
in Title Case and twelve write them in CAPS; matching only CAPS finds the modern
exams and silently drops two thirds of the archive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

Format = Literal["peca", "dissertativa"]

# "PADRÃO DE RESPOSTA" + en-dash/em-dash/hyphen + (PEÇA PROFISSIONAL | QUESTÃO N),
# with an optional trailing booklet code ("– B005250") that is not part of the number.
_SECTION = re.compile(
    r"PADR[ÃA]O\s+DE\s+RESPOSTA\s*[–—-]\s*"
    r"(?:(?P<peca>PE[ÇC]A\s+PROFISSIONAL)|QUEST[ÃA]O\s*0*(?P<number>\d+))"
    r"[^\n]*",
    re.I,
)
_ENUNCIADO = re.compile(r"^[ \t]*ENUNCIADO[ \t]*$", re.I | re.M)
_COMENTADO = re.compile(r"^[ \t]*GABARITO\s+COMENTADO[ \t]*$", re.I | re.M)

# Running headers, footers and the boilerplate disclaimer. These repeat on every
# page, so without stripping they land in the middle of a stem.
_FURNITURE = re.compile(
    r"^\s*(?:"
    r"ORDEM DOS ADVOGADOS DO BRASIL.*"
    r"|Padr[ãa]o de Resposta da Prova.*"
    r"|P[áa]gina\s+\d+\s+de\s+\d+.*"
    r"|Prova\s+Pr[áa]tico-?[Pp]rofissional.*"
    r"|[ÁA]REA:.*"
    r"|Aplicada\s+em\s+\d.*"
    r"|\d+[ºo]\s+Exame\s+de\s+Ordem.*"
    r"|[IVXLC]+\s+EXAME\s+DE\s+ORDEM.*"
    r"|EXAME\s+DE\s+ORDEM\s+UNIFICADO.*"
    r"|.*gabarito\s+preliminar.*"
    r"|.*mera\s+coincid[êe]ncia.*"
    r")\s*$",
    re.I | re.M,
)

# A section needs both halves to be a question. Below this many characters the
# enunciado did not extract (it is rasterised, or predates the sub-header
# convention) and the section must be skipped rather than stored with an empty stem.
_MIN_CHARS = 80


@dataclass(frozen=True)
class Section:
    number: str          # "peca" | "1" .. "4"
    format: Format
    stem: str
    rationale: str

    @property
    def usable(self) -> bool:
        return len(self.stem) >= _MIN_CHARS and len(self.rationale) >= _MIN_CHARS


def segment_padrao(text: str) -> list[Section]:
    """Split a padrão into its peça and questões. Pure: no I/O."""
    anchors = list(_SECTION.finditer(text))
    if not anchors:
        return []

    sections: list[Section] = []
    for i, m in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
        body = text[m.end() : end]
        stem, rationale = _split_body(body)
        sections.append(
            Section(
                number="peca" if m.group("peca") else str(int(m.group("number"))),
                format="peca" if m.group("peca") else "dissertativa",
                stem=stem,
                rationale=rationale,
            )
        )
    return sections


def _split_body(body: str) -> tuple[str, str]:
    """Enunciado -> stem, Gabarito Comentado -> rationale.

    Three shapes occur. Both sub-headers present is the common one. Older exams
    print only the reasoning, with no Enunciado header, so the stem comes out
    empty and the quality gate marks the section unusable.
    """
    enun, comen = _ENUNCIADO.search(body), _COMENTADO.search(body)
    if enun and comen and enun.start() < comen.start():
        return _clean(body[enun.end() : comen.start()]), _clean(body[comen.end() :])
    if comen:
        return _clean(body[: comen.start()]), _clean(body[comen.end() :])
    if enun:
        return _clean(body[enun.end() :]), ""
    return "", _clean(body)


def _clean(block: str) -> str:
    return "\n".join(
        line for line in _FURNITURE.sub("", block).splitlines() if line.strip()
    ).strip()
