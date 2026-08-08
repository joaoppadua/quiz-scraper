"""Text extraction and text-layer health (spec §13).

The spec's rule is "no text layer ⇒ log needs_ocr and skip". Reality has a third
state: a PDF can carry a *full* text layer that is entirely unmapped glyphs, so an
emptiness check waves it through and the corpus ingests noise. Health is therefore
three-valued, and no OCR is involved — every OAB padrão sampled was born-digital.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Health = Literal["ok", "no_text_layer", "glyph_unmapped"]

_CID = re.compile(r"\(cid:\d+\)")
# Latin-1 Supplement / Extended-A letters that pt-BR does not use but a shifted
# glyph map produces in bulk: "Ordem dos Advogados" -> "KƌĚĞŵ ĚŽƐ ĚǀŽŐĂĚŽƐ".
_MOJIBAKE = re.compile(r"[Ā-ɏͰ-Ͽ]")
_NOISE_THRESHOLD = 0.20


def extract_text(source: Path | str | bytes) -> str:
    """All pages of a PDF, joined with newlines.

    Never raises: a malformed document must not abort a 45-file harvest. Returns
    "" instead, which `text_health` then classifies as `no_text_layer`.
    """
    import pdfplumber

    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        with pdfplumber.open(stream) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        log.warning("could not extract text from %s: %s", _name(source), exc)
        return ""


def text_health(text: str) -> Health:
    """Judge whether extracted text is usable, empty, or garbage."""
    if not text.strip():
        return "no_text_layer"
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "no_text_layer"
    noise = len(_CID.findall(text)) * 6 + len(_MOJIBAKE.findall(text))
    return "glyph_unmapped" if noise / len(letters) > _NOISE_THRESHOLD else "ok"


def _name(source: Path | str | bytes) -> str:
    return "<bytes>" if isinstance(source, bytes) else str(source)
