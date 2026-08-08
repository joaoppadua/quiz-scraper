"""PDF text extraction and text-layer health.

No exam PDF is committed: this repo is public, and the padrão fixtures it needs are
kept as extracted text (see tests/fixtures/oab_site/*.txt). The one thing that
genuinely requires a PDF — that pdfplumber pulls text out of one — is exercised
against a minimal synthetic document built here.
"""

from pathlib import Path

from bqpp.parse.pdf import extract_text, text_health

FIX = Path(__file__).parent / "fixtures" / "oab_site"


def _one_page_pdf(text: str) -> bytes:
    """A valid single-page PDF containing `text`, with a correct xref table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
            b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>"
        ),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref,
    )
    return bytes(out)


def test_extracts_text_from_a_pdf(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_one_page_pdf("PADRAO DE RESPOSTA - QUESTAO 1"))
    assert "PADRAO DE RESPOSTA" in extract_text(pdf)


def test_accepts_bytes_as_well_as_a_path(tmp_path):
    payload = _one_page_pdf("Gabarito Comentado")
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(payload)
    assert extract_text(payload) == extract_text(pdf)
    assert "Gabarito Comentado" in extract_text(payload)


def test_extracted_pdf_text_is_healthy(tmp_path):
    assert text_health(extract_text(_one_page_pdf("Considerando o Art. 312 do CPP"))) == "ok"


def test_a_real_padrao_extract_is_healthy():
    """The committed fixture is the text pdfplumber produced from the 44º padrão."""
    text = (FIX / "padrao_44.txt").read_text(encoding="utf-8")
    assert len(text) > 15_000
    assert "PADRÃO DE RESPOSTA" in text
    assert text_health(text) == "ok"


def test_empty_text_is_no_text_layer():
    assert text_health("") == "no_text_layer"
    assert text_health("   \n\n \t ") == "no_text_layer"


def test_cid_noise_is_glyph_unmapped():
    """A PDF with a font but no /ToUnicode map extracts as (cid:N) runs."""
    assert text_health("(cid:3)(cid:17)(cid:9) " * 40) == "glyph_unmapped"


def test_mojibake_is_glyph_unmapped():
    """The other shape of the same failure: a wrong glyph map, not a missing one.
    'KƌĚĞŵ ĚŽƐ ĚǀŽŐĂĚŽƐ' is what 'Ordem dos Advogados' becomes."""
    assert text_health("KƌĚĞŵ ĚŽƐ ĚǀŽŐĂĚŽƐ ĚŽ ƌĂƐŝů " * 20) == "glyph_unmapped"


def test_a_few_odd_glyphs_do_not_condemn_a_document():
    """Legitimate Wingdings bullets and symbols sit well under the threshold."""
    healthy = "Considerando o Art. 312 do CPP, o examinando deve sustentar a tese. " * 20
    assert text_health(healthy + "(cid:9)(cid:12)") == "ok"


def test_a_broken_pdf_returns_empty_rather_than_raising(tmp_path):
    """extract_text must never abort a 45-document harvest; text_health judges."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not really a pdf")
    assert extract_text(broken) == ""
    assert text_health(extract_text(broken)) == "no_text_layer"
