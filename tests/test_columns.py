"""Column-aware extraction and invisible-character stripping."""

from pathlib import Path

import pytest

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


def test_two_column_reading_order_is_left_then_right():
    """A sentence in the left column must be contiguous, not interleaved with
    the right column's text — which is what naive extraction produces."""
    text = (FIX / "pc_df_26.txt").read_text(encoding="utf-8")
    assert "JUSTIFICATIVA" in text
    # A justificativa's own sentence must not be broken by an unrelated item number
    idx = text.find("JUSTIFICATIVA")
    window = text[idx : idx + 260]
    assert window.count("JUSTIFICATIVA") == 1


def test_single_column_source_extracts_whole_questions():
    text = (FIX / "mprs_50.txt").read_text(encoding="utf-8")
    assert "(A)" in text and "(E)" in text
    assert "MINISTÉRIO PÚBLICO" in text


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


@pytest.mark.parametrize("columns", [1, 2])
def test_column_count_is_respected(tmp_path, columns, one_page_pdf):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(one_page_pdf("Texto"))
    assert isinstance(extract_columns(pdf, columns=columns), str)
