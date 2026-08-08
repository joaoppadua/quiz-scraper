"""PDF text extraction and text-layer health. Reads a committed real padrão."""

from pathlib import Path

from bqpp.parse.pdf import extract_text, text_health

FIX = Path(__file__).parent / "fixtures" / "oab_site"
PADRAO_44 = FIX / "padrao_44.pdf"


def test_extracts_a_real_padrao():
    text = extract_text(PADRAO_44)
    assert len(text) > 15_000
    assert "PADRÃO DE RESPOSTA" in text
    assert "GABARITO COMENTADO" in text.upper()


def test_accepts_bytes_as_well_as_a_path():
    assert extract_text(PADRAO_44.read_bytes()) == extract_text(PADRAO_44)


def test_a_real_padrao_is_healthy():
    assert text_health(extract_text(PADRAO_44)) == "ok"


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
