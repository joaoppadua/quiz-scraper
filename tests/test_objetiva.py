"""A–E prova reader and the two answer-grid conventions."""

from pathlib import Path

import pytest

from bqpp.parse.objetiva import GridError, read_grid, segment_objetiva

FIX = Path(__file__).parent / "fixtures" / "cebraspe"


@pytest.fixture(scope="module")
def mprs():
    return segment_objetiva((FIX / "mprs_50.txt").read_text(encoding="utf-8"))


# ---- item segmentation -----------------------------------------------------

def test_mprs_items_have_five_labelled_choices(mprs):
    assert mprs
    for it in mprs:
        assert len(it.choices) == 5
        assert [c["label"] for c in it.choices] == list("ABCDE")
        assert all(c["text"].strip() for c in it.choices)


def test_roman_sub_items_are_not_mistaken_for_choices(mprs):
    """MPRS stems enumerate I-, II-, III- inside the question; only (A)-(E) are choices."""
    with_roman = [i for i in mprs if "II -" in i.stem or "II-" in i.stem]
    assert with_roman, "the fixture must exercise this"
    for it in with_roman:
        assert len(it.choices) == 5


def test_stems_are_not_empty(mprs):
    assert all(it.stem.strip() for it in mprs)


def test_item_numbers_are_sequential_integers(mprs):
    numbers = [int(i.number) for i in mprs]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)


def test_lowercase_choice_labels_are_normalised():
    """MPRS labels alternatives (A)-(E); MPF labels them (a)-(d)."""
    text = (
        "102. Assinale a única alternativa correta:\n"
        "(a) O livre convencimento autoriza a convicção íntima.\n"
        "(b) O princípio da verdade real autoriza a produção da prova de ofício.\n"
        "(c) Terceira alternativa qualquer.\n"
        "(d) Quarta alternativa qualquer.\n"
    )
    (item,) = segment_objetiva(text)
    assert [c["label"] for c in item.choices] == ["A", "B", "C", "D"]
    assert item.number == "102"


def test_a_four_option_prova_is_usable():
    text = ("1. Pergunta?\n(a) um\n(b) dois\n(c) três\n(d) quatro\n")
    (item,) = segment_objetiva(text)
    assert item.usable and len(item.choices) == 4


# ---- grids: interleaved (MPRS / MPF) --------------------------------------

def test_mprs_grid_reads_every_answer():
    grid = read_grid((FIX / "mprs_50_grid.txt").read_text(encoding="utf-8"), style="interleaved")
    assert len(grid) == 100
    assert set(grid) == set(range(1, 101))


def test_mprs_annulled_items_are_detected():
    """ANULADA spelled out, not an X (amendment C12)."""
    grid = read_grid((FIX / "mprs_50_grid.txt").read_text(encoding="utf-8"), style="interleaved")
    annulled = sorted(n for n, v in grid.items() if v is None)
    assert annulled == [1, 18, 20, 35, 36, 45, 79]


def test_mpf_grid_reads_n_as_annulment():
    grid = read_grid((FIX / "mpf_31_gab.txt").read_text(encoding="utf-8"), style="interleaved")
    assert grid, "the MPF grid must parse"
    assert grid.get(91) is None and grid.get(95) is None, "N marks an annulled question"
    assert grid.get(1) == "B" and grid.get(2) == "A"


# ---- grids: paired rows (Cebraspe) ----------------------------------------

PAIRED = """\
Obs.: ( X ) item anulado.
   Item        1     2     3     4     5
Gabarito       C     E     E     C     X
   Item        6     7     8     9    10
Gabarito       E     C     C     E     E
"""


def test_paired_row_grid_zips_items_to_verdicts():
    """A per-line regex finds nothing here — the rows must be paired (C13)."""
    grid = read_grid(PAIRED, style="paired_rows")
    assert len(grid) == 10
    assert grid[1] == "C" and grid[3] == "E"
    assert grid[5] is None, "X marks an annulled item"


def test_a_misaligned_paired_row_is_an_error():
    bad = "   Item     1    2    3\nGabarito    C    E\n"
    with pytest.raises(GridError):
        read_grid(bad, style="paired_rows")


# ---- refusing to guess (amendment C12) ------------------------------------

def test_a_grid_with_no_recognisable_convention_raises():
    """Defaulting to 'nothing annulled' would silently ship wrong answer keys."""
    with pytest.raises(GridError):
        read_grid("nenhuma tabela aqui", style="interleaved")


def test_an_unknown_style_is_rejected():
    with pytest.raises(GridError):
        read_grid(PAIRED, style="telepathy")


def test_cover_page_instructions_are_not_mistaken_for_questions():
    """A prova's cover carries its own enumerated instructions; anchoring on the
    first candidate swallows the opening questions into one oversized item."""
    text = (
        "1. NÃO HAVERÁ SUBSTITUIÇÃO da folha de respostas.\n"
        "2. A folha será corrigida por leitora óptica.\n"
        "a) Reveja as questões.\n"
        "b) Use caneta esferográfica.\n"
        "1. Primeira questão real?\n(a) um\n(b) dois\n(c) três\n(d) quatro\n"
        "2. Segunda questão real?\n(a) um\n(b) dois\n(c) três\n(d) quatro\n"
        "3. Terceira questão real?\n(a) um\n(b) dois\n(c) três\n(d) quatro\n"
    )
    items = segment_objetiva(text)
    assert [i.number for i in items] == ["1", "2", "3"]
    assert all(len(i.choices) == 4 for i in items)
