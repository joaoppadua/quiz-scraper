"""A–E prova reader and the two answer-grid conventions."""

import hashlib
import json
from pathlib import Path

import pytest

from bqpp.parse.objetiva import _ITEM_STYLES, GridError, read_grid, segment_objetiva

FIX = Path(__file__).parent / "fixtures" / "cebraspe"
OAB_FIX = Path(__file__).parent / "fixtures" / "oab_1f"


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


# ---- selectable item anchor (M2.5, amendments E12/E13) ---------------------


def test_item_styles_has_the_three_keys_task_5_and_6_select_among():
    assert set(_ITEM_STYLES) == {"punctuated", "bare", "questao"}


def test_an_unknown_item_style_raises():
    """Doctrine: refusing beats defaulting — a wrong parse ships wrong answers."""
    text = "1. Pergunta?\n(a) um\n(b) dois\n(c) três\n(d) quatro\n"
    with pytest.raises(ValueError):
        segment_objetiva(text, item_style="telepathy")


def test_oab_exame43_default_style_finds_nothing():
    """The OAB caderno numbers items with a bare numeral; the shipped anchor requires
    a trailing '.' or ')' and so must not match it at all (E13)."""
    text = (OAB_FIX / "exame_43_tipo1.txt").read_text(encoding="utf-8")
    assert segment_objetiva(text) == []


def test_oab_exame43_bare_anchor_yields_the_winning_run_55_to_74():
    """Item 1 is a deliberate negative control: annulled, and sitting outside the
    winning run so the longest-sequential-run arbiter drops it (Task 1 report §5)."""
    text = (OAB_FIX / "exame_43_tipo1.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text, item_style="bare")
    numbers = [i.number for i in items]
    assert [int(n) for n in numbers] == list(range(55, 75))
    assert "1" not in numbers, "item 1 must not survive — it is the negative control"
    for it in items:
        assert it.usable

    # Every item, including the last, has exactly its own four choices labelled
    # A-D. `.usable` alone (>= 4 choices) is too weak to see the class of defect
    # this guards against — see the next test.
    for it in items:
        assert [c["label"] for c in it.choices] == list("ABCD")


def test_oab_exame43_last_item_does_not_absorb_the_trailing_questionario():
    """The fixture's item 74 is followed directly by the questionário de percepção,
    and once `_CHOICE` tolerates a bare "A)" (E12) the questionário's own ten
    bare-lettered alternatives would read as 40 further choices of item 74 unless
    the last item's body is bounded against them — verified corrupting all 19
    in-scope exams' true last item before the fix (Task 2 fix-round report §2). A
    weak `.usable` check (>= 4 choices) does not see this: it asserts choice
    *content*, so a boundary regression that reintroduces contamination fails here
    even if some other, unrelated line happens to keep the count at exactly 4."""
    text = (OAB_FIX / "exame_43_tipo1.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text, item_style="bare")
    last = items[-1]
    assert last.number == "74"
    assert len(last.choices) == 4
    assert last.stem.startswith("Em sede de sentença prolatada")

    # The real choices, verbatim from the caderno.
    assert last.choices[0]["text"].startswith(
        "Dada a concomitância de motivações para o término motivado"
    )
    assert last.choices[2]["text"].startswith("Configurada a culpa recíproca")

    # None of the questionário's own ten alternatives — the actual contamination
    # this fix removes — leaked into item 74's choices.
    joined = " ".join(c["text"] for c in last.choices)
    for contaminant in (
        "muito fácil",
        "Plenamente satisfatória",
        "muito longa",
        "Sim, todos",
        "Não tenho como opinar",
    ):
        assert contaminant not in joined, f"questionário content leaked in: {contaminant!r}"


def test_oab_exame43_questionario_restart_does_not_win():
    """The trailing questionário de percepção restarts its own 1..10 numbering; the
    real 20-item prova run must still win (E4)."""
    text = (OAB_FIX / "exame_43_tipo1.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text, item_style="bare")
    assert len(items) == 20, "the questionário's run of 10 must lose to the prova's run of 20"


def test_oab_exame42_bare_anchor_with_unparenthesised_choices():
    """15812/40º/16483-style exams write 'A)' with no opening parenthesis anywhere
    in the caderno — 36 bare markers, zero parenthesised (E12). This fixture ends
    right after item 48 with no trailing questionário, so its last item was never
    contaminated — but every item, including the last, is asserted here too."""
    text = (OAB_FIX / "exame_42_tipo1.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text, item_style="bare")
    assert [int(i.number) for i in items] == list(range(40, 49))
    for it in items:
        assert it.usable
        assert [c["label"] for c in it.choices] == list("ABCD")
    assert len(items[-1].choices) == 4, "the last item (48) must be clean too"


def test_oab_exame42_default_and_questao_styles_find_nothing():
    text = (OAB_FIX / "exame_42_tipo1.txt").read_text(encoding="utf-8")
    assert segment_objetiva(text) == []
    assert segment_objetiva(text, item_style="questao") == []


def test_oab_exame29_questao_anchor_yields_60_to_68():
    """11561/XXIX-style exams anchor on 'Questão N' alone on its own line, not a
    bare numeral (E13). This fixture ends right after item 68 with no trailing
    questionário, so its last item was never contaminated — but every item,
    including the last, is asserted here too."""
    text = (OAB_FIX / "exame_29_tipo1.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text, item_style="questao")
    assert [int(i.number) for i in items] == list(range(60, 69))
    for it in items:
        assert it.usable
        assert [c["label"] for c in it.choices] == list("ABCD")
    assert len(items[-1].choices) == 4, "the last item (68) must be clean too"


def test_oab_exame29_default_style_finds_nothing():
    text = (OAB_FIX / "exame_29_tipo1.txt").read_text(encoding="utf-8")
    assert segment_objetiva(text) == []


# ---- the anchor stays selectable: a bare anchor catastrophically collapses a
# ---- punctuated source (31/50 real items -> single digits) -----------------
#
# The load-bearing regression is the `punctuated`-style item COUNT and full parsed
# STRUCTURE staying at 50/31 (see the signature tests below) — that is what
# protects shipped data. The `bare`-style residue below is evidence, not a
# contract: what matters is that it collapses to a handful of items out of a
# punctuated 50/31, not the exact surviving digit. The last-item boundary fix
# (fix round 1) moved MPF's bare-style residue from 9 to 8 items — a stray
# bare-shaped candidate deep in MPF's own unrelated (punctuated) exam content
# now also bounds its degenerate last item — and that is fine: MPF is not a
# bare-anchored source and this run was never meaningful data, only a canary.


def test_mprs_bare_anchor_collapses_a_punctuated_source():
    """Pinned so a future merge of the bare and punctuated patterns fails loudly."""
    text = (FIX / "mprs_50.txt").read_text(encoding="utf-8")
    assert len(segment_objetiva(text, item_style="punctuated")) == 50
    bare = segment_objetiva(text, item_style="bare")
    assert len(bare) == 1, "50 real items must not survive under the wrong anchor"


def test_mprs_finds_nothing_under_the_questao_anchor():
    text = (FIX / "mprs_50.txt").read_text(encoding="utf-8")
    assert segment_objetiva(text, item_style="questao") == []


def test_mpf_bare_anchor_collapses_a_punctuated_source():
    """Pinned so a future merge of the bare and punctuated patterns fails loudly.
    The surviving count (8, post-fix — see the module comment above) is evidence
    of the collapse, not a contract: what matters is single digits out of 31."""
    text = (FIX / "mpf_31.txt").read_text(encoding="utf-8")
    assert len(segment_objetiva(text, item_style="punctuated")) == 31
    bare = segment_objetiva(text, item_style="bare")
    assert len(bare) < 10, "31 real items must not survive under the wrong anchor"
    assert [int(i.number) for i in bare] == list(range(1, len(bare) + 1)), "still contiguous from 1"


def test_mpf_finds_nothing_under_the_questao_anchor():
    text = (FIX / "mpf_31.txt").read_text(encoding="utf-8")
    assert segment_objetiva(text, item_style="questao") == []


# ---- regression: MPRS/MPF parse identically to before this change ----------


def _structure_signature(items) -> str:
    """A stable digest over (number, stem, choices) — the whole parsed structure,
    not just a count — so widening _CHOICE's marker cannot silently change content."""
    payload = [
        {"number": it.number, "stem": it.stem, "choices": [(c["label"], c["text"]) for c in it.choices]}
        for it in items
    ]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_mprs_parses_byte_identically_after_the_choice_marker_widened(mprs):
    """Signature captured from the pre-M2.5 code (unmodified _CHOICE, no item_style
    argument at all), before _CHOICE's opening parenthesis was made optional (E12)."""
    assert len(mprs) == 50
    assert [int(i.number) for i in mprs] == list(range(1, 51))
    assert (
        _structure_signature(mprs)
        == "8513b3815bcd7d42cee88ec2ced8bc3276b228eda8456cc1b6ef6cf00e6a3b87"
    )


def test_mpf_parses_byte_identically_after_the_choice_marker_widened():
    """Same pin as above, for the MPF fixture (31 items, lowercase (a)-(d) labels)."""
    text = (FIX / "mpf_31.txt").read_text(encoding="utf-8")
    items = segment_objetiva(text)
    assert len(items) == 31
    assert [int(i.number) for i in items] == list(range(1, 32))
    assert (
        _structure_signature(items)
        == "ad1e6848b87c16e2dc05da8d64eff4496f44f0e5cea780421e58a02ba701bbb6"
    )
