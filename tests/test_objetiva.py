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


# ---- the grid must not scavenge cells out of prose -------------------------

PROSE = """\
As questões 1 a 14 referem-se ao texto acima.
43 A Inquisição constituiu-se em tribunal eclesiástico.
(C) Apenas 3 e 4 estão corretas.
Considere as linhas 5, 11, 12 e 13 do texto.
"""

# A complete interleaved grid, laid out in four columns exactly as MPRS prints it.
# Complete, because a real grid covers 1..N — a truncated excerpt is not one.
REAL_GRID = """\
 1 ANULADA    4    A       7   C     10    E
 2    E       5    C       8   E     11    C
 3    A       6    D       9   D     12    B
"""


def test_prose_alone_is_not_a_grid():
    """A prova body contains number-letter pairs everywhere: 'As questões 1 a 14',
    '(C) Apenas 3 e 4', margin line numbers. None of it is an answer grid."""
    with pytest.raises(GridError):
        read_grid(PROSE, style="interleaved")


def test_a_grid_after_prose_wins_over_the_prose():
    """MPRS ships prova and grid in one file, and the grid is on the last page."""
    grid = read_grid(PROSE + REAL_GRID, style="interleaved")
    assert grid[1] is None, "item 1 is ANULADA, not 'A' scavenged from line 43"
    assert grid[2] == "E" and grid[3] == "A"
    assert 43 not in grid or grid[43] != "A", "the passage line number is not an answer"


def test_lowercase_prose_letters_are_not_answers():
    with pytest.raises(GridError):
        read_grid("o item 5 e o item 7 a seguir", style="interleaved")


def test_annulled_is_matched_before_the_letter_a():
    """'1 ANULADA' must not split as ('1','A')."""
    grid = read_grid(REAL_GRID, style="interleaved")
    assert grid[1] is None


def test_a_non_contiguous_recovery_is_rejected():
    """Real grids cover 1..N. Scattered numbers mean we matched something else."""
    with pytest.raises(GridError):
        read_grid("7 A\n41 B\n99 C\n", style="interleaved")


def test_the_last_question_does_not_swallow_the_answer_grid():
    """A single-file prova carries its grid after the last question; without a bound
    the final alternative absorbs it and renders it above the spoiler block."""
    text = (
        "1. Pergunta?\n(A) um\n(B) dois\n(C) três\n(D) quatro\n(E) cinco\n"
        " 1 ANULADA    4    A       7   C     10    E\n"
        " 2    E       5    C       8   E     11    C\n"
    )
    (item,) = segment_objetiva(text)
    assert "ANULADA" not in item.choices[-1]["text"]
    assert item.choices[-1]["text"] == "cinco"


def test_running_heads_are_stripped_from_stems_and_choices():
    text = (
        "1. Pergunta?\n(A) um\nMINISTÉRIO PÚBLICO DO ESTADO DO RIO GRANDE DO SUL\n"
        "(B) dois\nPágina 43\n(C) três\n(D) quatro\n(E) cinco\n"
    )
    (item,) = segment_objetiva(text)
    joined = " ".join(c["text"] for c in item.choices)
    assert "MINISTÉRIO" not in joined and "Página" not in joined


def test_per_source_furniture_comes_from_the_manifest():
    text = "1. Pergunta?\n(A) um\nTRIBUNAL REGIONAL QUALQUER\n(B) dois\n(C) três\n(D) quatro\n"
    (item,) = segment_objetiva(text, furniture=["TRIBUNAL REGIONAL"])
    assert "TRIBUNAL" not in " ".join(c["text"] for c in item.choices)
