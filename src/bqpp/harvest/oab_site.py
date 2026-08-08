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
from datetime import date

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
