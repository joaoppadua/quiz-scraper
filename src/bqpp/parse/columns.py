"""Column-aware text extraction (spec §13, amended).

`parse.pdf.extract_text` reads a page as one block, which is correct for the OAB
padrões but wrong for almost everything M3 harvests: Cebraspe cadernos and the MPF
prova are two-column, and reading them linearly splices one column's sentences into
the other's. Cropping each page at its midpoint and reading left-then-right recovers
all of them — measured against every seed document, and enough that a general
word-clustering splitter is not needed.

Also strips Unicode format characters: the MPF prova carries 344 invisible ones,
which make keyword matching silently fail.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

from bqpp.parse.pdf import _name, _open_stream

log = logging.getLogger(__name__)

# pdfplumber inserts a space when the gap between two characters exceeds this. The
# default of 3 is too wide for the MPF prova's tight typesetting, which comes out as
# "Assinaleaopçãocorreta:" — unreadable in a shortlist and useless to the classifier.
# 1.5 cuts glued runs from 13 to 1 on a sampled MPF page and leaves MPRS and both
# Cebraspe cadernos byte-identical.
_X_TOLERANCE = 1.5


def strip_format_chars(text: str) -> str:
    """Remove Unicode `Cf` characters (zero-width joiners, BOMs, bidi marks)."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def extract_columns(source: Path | str | bytes, *, columns: int = 2) -> str:
    """Page text in reading order for a `columns`-column layout.

    Never raises: a malformed document must not abort a harvest.
    """
    import pdfplumber

    try:
        with pdfplumber.open(_open_stream(source)) as pdf:
            parts: list[str] = []
            for page in pdf.pages:
                if columns <= 1:
                    parts.append(page.extract_text(x_tolerance=_X_TOLERANCE) or "")
                    continue
                width = page.width / columns
                for i in range(columns):
                    box = (i * width, 0, (i + 1) * width, page.height)
                    parts.append(
                        page.crop(box).extract_text(x_tolerance=_X_TOLERANCE) or ""
                    )
            return strip_format_chars("\n".join(parts))
    except Exception as exc:
        log.warning("could not extract columns from %s: %s", _name(source), exc)
        return ""
