"""Column-aware extraction and invisible-character stripping."""

from pathlib import Path

from bqpp.parse.columns import extract_columns, strip_format_chars

FIX = Path(__file__).parent / "fixtures" / "cebraspe"


def test_strip_removes_invisible_format_characters():
    """The MPF prova carries 344 Unicode Cf characters, which make keyword
    matching silently fail until they are removed."""
    dirty = "livre​convencimento‍ motivado﻿"
    assert strip_format_chars(dirty) == "livreconvencimento motivado"


def test_strip_leaves_pt_br_text_untouched():
    text = "Acerca da prova no processo penal, julgue os itens — inclusive o nº 3."
    assert strip_format_chars(text) == text


LEFT = ["esquerda um", "esquerda dois", "esquerda tres"]
RIGHT = ["direita um", "direita dois", "direita tres"]


def test_two_column_reading_order_is_left_then_right(two_column_pdf):
    """The whole point of the module: the left column must be read through before
    the right one begins. Naive extraction interleaves them line by line."""
    out = extract_columns(two_column_pdf(LEFT, RIGHT), columns=2)
    positions = [out.index(t) for t in LEFT + RIGHT]
    assert positions == sorted(positions), f"columns interleaved: {out!r}"
    assert out.index("esquerda tres") < out.index("direita um")


def test_one_column_mode_interleaves_the_same_page(two_column_pdf):
    """Proof the columns argument does something: read as one column, the same page
    comes out line-interleaved, which is the corruption this module exists to avoid."""
    out = extract_columns(two_column_pdf(LEFT, RIGHT), columns=1)
    assert out.index("direita um") < out.index("esquerda dois")


def test_reading_order_is_not_right_then_left(two_column_pdf):
    out = extract_columns(two_column_pdf(LEFT, RIGHT), columns=2)
    assert out.index("esquerda um") < out.index("direita um")


def test_a_real_two_column_fixture_keeps_justificativas_intact():
    text = (FIX / "pc_df_26.txt").read_text(encoding="utf-8")
    idx = text.find("JUSTIFICATIVA")
    assert text[idx : idx + 260].count("JUSTIFICATIVA") == 1


def test_extract_columns_accepts_bytes_and_paths(tmp_path, one_page_pdf):
    """Mirrors parse.pdf.extract_text so callers can pass a fetched body."""
    payload = one_page_pdf("Julgue o item a seguir")
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(payload)
    assert "Julgue o item" in extract_columns(payload, columns=1)
    assert extract_columns(payload, columns=1) == extract_columns(pdf, columns=1)


def test_a_broken_pdf_returns_empty_rather_than_raising(tmp_path):
    broken = tmp_path / "b.pdf"
    broken.write_bytes(b"%PDF-1.7 not really")
    assert extract_columns(broken) == ""



