"""The padrão de resposta segmenter, against real extracted text from three eras."""

import json
from pathlib import Path

import pytest

from bqpp.parse.padrao import segment_padrao

FIX = Path(__file__).parent / "fixtures" / "oab_site"


@pytest.fixture(scope="module")
def sections_44():
    return segment_padrao((FIX / "padrao_44.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sections_xxxiii():
    return segment_padrao((FIX / "padrao_xxxiii.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sections_xxv():
    return segment_padrao((FIX / "padrao_xxv.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sections_vii():
    return segment_padrao((FIX / "padrao_vii.txt").read_text(encoding="utf-8"))


# ---- the acceptance fixture (spec §13) ------------------------------------

def test_matches_the_hand_checked_expectation(sections_44):
    expected = json.loads((FIX / "padrao_44.expected.json").read_text(encoding="utf-8"))
    got = [
        {
            "number": s.number,
            "format": s.format,
            "usable": s.usable,
            "stem_head": " ".join(s.stem.split())[:80],
            "rationale_head": " ".join(s.rationale.split())[:80],
        }
        for s in sections_44
    ]
    assert got == expected


def test_a_modern_padrao_yields_one_peca_and_four_questions(sections_44):
    assert len(sections_44) == 5
    assert [s.format for s in sections_44] == ["peca"] + ["dissertativa"] * 4
    assert [s.number for s in sections_44] == ["peca", "1", "2", "3", "4"]
    assert all(s.usable for s in sections_44)


# ---- amendment B7: case, dashes, padding, booklet codes -------------------

def test_title_case_sub_headers_are_found(sections_xxxiii):
    """XXXIII writes 'Enunciado'/'Gabarito Comentado', not ALL CAPS. A
    case-sensitive regex passes the 44º and silently loses two thirds of the
    archive here — this is the regression test for that."""
    assert len(sections_xxxiii) == 5
    assert all(s.usable for s in sections_xxxiii)
    assert all(s.stem and s.rationale for s in sections_xxxiii)


def test_booklet_code_suffix_is_stripped_from_the_number(sections_xxv):
    """XXV headers read 'PADRÃO DE RESPOSTA – QUESTÃO 1 – B005250'."""
    assert [s.number for s in sections_xxv] == ["peca", "1", "2", "3", "4"]


def test_hyphen_and_en_dash_headers_both_segment(sections_xxv):
    """XXV mixes '-' on the peça with '–' on the questões, in one document."""
    assert len(sections_xxv) == 5
    assert sections_xxv[0].format == "peca"


def test_zero_padded_numbers_normalise():
    text = (
        "PADRÃO DE RESPOSTA – QUESTÃO 01\nEnunciado\n" + "a" * 200 +
        "\nGabarito Comentado\n" + "b" * 200
    )
    assert [s.number for s in segment_padrao(text)] == ["1"]


# ---- amendment B9: the quality gate ---------------------------------------

def test_a_document_with_no_anchors_yields_nothing():
    assert segment_padrao("ORDEM DOS ADVOGADOS DO BRASIL\nalgum texto solto") == []
    assert segment_padrao("") == []


# ---- content fidelity (spec §15: verbatim, never paraphrased) -------------

def test_page_furniture_is_stripped(sections_44):
    for s in sections_44:
        body = s.stem + s.rationale
        for junk in ("ORDEM DOS ADVOGADOS DO BRASIL", "Página 1 de", "ÁREA: DIREITO PENAL",
                     "Aplicada em", "gabarito preliminar"):
            assert junk not in body, f"{junk!r} leaked into section {s.number}"


def test_sub_item_markers_and_point_values_survive_verbatim(sections_44):
    q1 = next(s for s in sections_44 if s.number == "1")
    assert "A)" in q1.stem and "B)" in q1.stem
    assert "(Valor:" in q1.stem


def test_the_rationale_is_the_bancas_own_reasoning(sections_44):
    q1 = next(s for s in sections_44 if s.number == "1")
    assert "consunção" in q1.rationale.lower()
    assert "28-A" in q1.rationale, "cites the ANPP article the banca relied on"


def test_stem_and_rationale_do_not_overlap(sections_44):
    for s in sections_44:
        assert s.stem not in s.rationale
        assert "Gabarito Comentado" not in s.stem


# ---- regressions found by the M2 adversarial review -----------------------

@pytest.mark.parametrize("fixture", ["padrao_xxxiii.txt", "padrao_xxv.txt", "padrao_vii.txt"])
def test_page_furniture_is_stripped_from_every_era(fixture):
    """The 44º happens to contain neither footer form, so asserting only against it
    proved nothing. XXXIII and XXV splice `Padrão de Resposta Página 1 de 12` and the
    disclaimer's continuation line straight into the middle of a legal fact pattern."""
    import re

    leak = re.compile(
        r"(P[áa]gina\s+\d+\s*(?:de|/)\s*\d+|podendo ser alterado|Padr[ãa]o de Resposta P)", re.I
    )
    text = (FIX / fixture).read_text(encoding="utf-8")
    for s in segment_padrao(text):
        assert not leak.search(s.stem), f"furniture in stem of {fixture} §{s.number}"
        assert not leak.search(s.rationale), f"furniture in rationale of {fixture} §{s.number}"


def test_colon_suffixed_sub_headers_are_recognised(sections_vii):
    """VII writes `Enunciado:` and `Gabarito comentado:`. Anchoring to end-of-line
    without tolerating the colon dropped the whole pre-2013 era, and — worse — glued
    the enunciado into the rationale field."""
    assert len(sections_vii) == 5
    assert all(s.usable for s in sections_vii)
    for s in sections_vii:
        assert "Gabarito comentado" not in s.stem
        assert s.stem and s.rationale


def test_a_rationale_only_section_is_still_unusable():
    """The quality gate must still fire when there genuinely is no enunciado."""
    text = "PADRÃO DE RESPOSTA – QUESTÃO 1\n" + "Resposta esperada. " * 30
    (section,) = segment_padrao(text)
    assert not section.usable
    assert section.stem == ""
