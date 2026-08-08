"""The OAB exam-index scraper, against trimmed real pages. No network."""

from datetime import date
from pathlib import Path

import pytest

from bqpp.harvest.oab_site import (
    parse_exam_ids,
    parse_exam_index,
    select_penal_padrao,
)

FIX = Path(__file__).parent / "fixtures" / "oab_site"


@pytest.fixture
def seed_html():
    return (FIX / "seed_page.html").read_text(encoding="utf-8")


@pytest.fixture
def index_44():
    return (FIX / "exam_index_44.html").read_text(encoding="utf-8")


@pytest.fixture
def index_xxv():
    return (FIX / "exam_index_xxv.html").read_text(encoding="utf-8")


# ---- discovery -------------------------------------------------------------

def test_seed_page_yields_every_exam(seed_html):
    exams = parse_exam_ids(seed_html)
    assert len(exams) == 46, "46 real exams; the 47th option is the placeholder"
    assert all(e.id != "0" for e in exams), "the 'Selecione o exame' placeholder must be dropped"


def test_exam_ids_carry_their_label(seed_html):
    by_id = {e.id: e.label for e in parse_exam_ids(seed_html)}
    assert by_id["17000"] == "44º EXAME DE ORDEM UNIFICADO"
    assert by_id["11535"] == "EXAME DE ORDEM UNIFICADO 2010.2"


# ---- per-exam index --------------------------------------------------------

def test_exam_index_finds_every_pdf(index_44):
    assert len(parse_exam_index(index_44)) == 41


def test_index_entries_parse_their_date(index_44):
    entries = parse_exam_index(index_44)
    caderno = next(e for e in entries if "Caderno de Provas (Direito Penal)" in e.label)
    assert caderno.date == date(2025, 10, 19)


def test_index_labels_are_unescaped_and_whitespace_collapsed(index_44):
    labels = [e.label for e in parse_exam_index(index_44)]
    assert any(lb == "19/10/2025 - Caderno de Provas (Direito Penal)" for lb in labels)


# ---- selecting the Penal padrão (amendment B10) ----------------------------

def test_definitivo_wins_over_the_plain_padrao(index_44):
    entry, rung = select_penal_padrao(parse_exam_index(index_44))
    assert rung == "definitivo"
    assert "definitivo" in entry.label.lower()
    assert entry.href.endswith("b9ef8314-0502-4cb7-a0ce-4f111571aa46.pdf")


def test_falls_back_to_the_plain_padrao(index_44):
    """13 of 46 exams never published a definitivo; they must not be skipped."""
    entries = [e for e in parse_exam_index(index_44) if "definitivo" not in e.label.lower()]
    entry, rung = select_penal_padrao(entries)
    assert rung == "plain"
    assert "Padrão de respostas (Direito Penal)" in entry.label


def test_other_disciplines_are_never_selected(index_44):
    entry, _ = select_penal_padrao(parse_exam_index(index_44))
    for other in ("Civil", "Tributário", "Administrativo", "Trabalho", "Empresarial",
                  "Constitucional"):
        assert other not in entry.label


def test_the_1a_fase_caderno_is_not_a_padrao(index_44):
    entry, _ = select_penal_padrao(parse_exam_index(index_44))
    assert "Tipo 1" not in entry.label
    assert "Caderno" not in entry.label


def test_no_penal_padrao_returns_none():
    assert select_penal_padrao([]) is None


@pytest.mark.parametrize(
    "label",
    [
        "11/11/2025 - Padrão de respostas definitivo (Direito Penal)",
        "19/10/2025 - Padrão de respostas (Direito Penal)",
        "05/05/2018 - Padrão de respostas - Direito Penal",
        "01/02/2016 - Padrão de Respostas Definitivo- Penal",
        "01/02/2016 - Padrão de Respostas Definitivo - Direito Penal",
        "01/02/2014 - Padrão de respostas - Penal",
    ],
)
def test_every_observed_label_spelling_is_matched(label):
    """Ten spellings occur across 15 years of exams; equality would miss eight."""
    from bqpp.harvest.oab_site import IndexEntry

    assert select_penal_padrao([IndexEntry(href="x.pdf", label=label)]) is not None


@pytest.mark.parametrize(
    "label",
    [
        "11/11/2025 - Padrão de respostas definitivo (Direito Civil)",
        "17/08/2025 - Caderno de Prova - Tipo 1",
        "19/10/2025 - Caderno de Provas (Direito Penal)",
        "03/09/2025 - Gabaritos Definitivos - Prova Objetiva (1ª fase)",
        "01/01/2020 - Resultado Final (após recursos)",
    ],
)
def test_non_padrao_labels_are_rejected(label):
    from bqpp.harvest.oab_site import IndexEntry

    assert select_penal_padrao([IndexEntry(href="x.pdf", label=label)]) is None


# ---- reaplicação variants (amendment B11) ---------------------------------

def test_reaplicacao_variants_are_kept_as_separate_entries(index_xxv):
    entries = parse_exam_index(index_xxv)
    penal = [e for e in entries if "Penal" in e.label and "Padrão" in e.label]
    assert len(penal) >= 2, "XXV published a Porto Alegre/RS reapplication"
    assert any(e.variant for e in penal), "the variant suffix must be captured, not dropped"


def test_variant_is_extracted_from_the_label():
    from bqpp.harvest.oab_site import IndexEntry

    e = IndexEntry(
        href="x.pdf",
        label="14/09/2018 - Padrão de respostas definitivo (Direito Penal) - Porto Alegre/RS",
    )
    assert e.variant == "Porto Alegre/RS"


def test_a_plain_label_has_no_variant():
    from bqpp.harvest.oab_site import IndexEntry

    assert IndexEntry(href="x.pdf", label="11/11/2025 - Padrão de respostas (Direito Penal)").variant is None


# ---- exam_year comes from the label, never the URL (amendment B4) ---------

def test_exam_year_comes_from_the_label_not_the_url_path(index_xxv):
    """Pre-2019 files were rehomed under /arquivos/2019/10/, so the path lies."""
    entry, _ = select_penal_padrao(parse_exam_index(index_xxv))
    assert entry.date is not None
    assert entry.date.year == 2018, "XXV was applied in 2018"
    assert "/2019/" in entry.href, "...even though the URL says 2019 (this is the point)"
