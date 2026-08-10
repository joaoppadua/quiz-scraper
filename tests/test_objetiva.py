"""A–E prova reader and the two answer-grid conventions.

Every "fix round" and "task review" referenced below is recorded in
`docs/superpowers/plans/2026-08-09-banco-questoes-pp-m2.5.md` **§ Defect history** —
what each round found, and why the shipped rule has the shape it does. That is the
tracked record; the per-task review reports themselves are working-tree-only.
"""

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


def test_an_alternation_inside_one_fragment_still_strips_whole_lines():
    """The fragments are composed into `^.*(?:{f}).*$`, and the group is the point.

    Ungrouped, a top-level `|` inside a fragment escapes the whole-line wrapper:
    `^.*RODAPE|CABECALHO.*$` matches the bare second word anywhere on a line and
    deletes only the matched fragment, leaving the rest of the sentence to ship as
    if it were verbatim. Both branches must remove the *line*, or a config edit
    that nobody could be expected to see as dangerous silently truncates legal
    text mid-sentence.
    """
    text = (
        "1. Pergunta?\n(A) alfa RODAPE meio da frase jurídica\n"
        "(B) beta CABECALHO resto da frase jurídica\n(C) três\n(D) quatro\n"
    )
    (item,) = segment_objetiva(text, furniture=[r"RODAPE|CABECALHO"])

    # Whole lines gone, so those two alternatives are left empty — not half-deleted.
    assert item.choices[0]["text"] == ""
    assert item.choices[1]["text"] == ""
    assert item.choices[2]["text"] == "três"


def test_a_malformed_furniture_fragment_names_itself():
    """`config/sources.yaml`'s furniture list is professor-editable and every shipped
    fragment carries regex metacharacters. A stray parenthesis must not surface as a
    bare `PatternError` that names neither the fragment nor anything else — it aborts
    the whole harvest, and the message is the only clue to which line to fix."""
    text = "1. Pergunta?\n(A) um\n(B) dois\n(C) três\n(D) quatro\n"
    with pytest.raises(ValueError, match=r"invalid furniture fragment '\^\[ \\\\t\]\*\('"):
        segment_objetiva(text, furniture=[r"^[ \t]*("])


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
    winning run so the longest-sequential-run arbiter drops it (amendment E16)."""
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
    in-scope exams' true last item before the fix (§ Defect history — Task 2). A
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


# ---- the tail-boundary check must not run against punctuated sources -------


def test_an_incidental_bare_numeral_inside_a_punctuated_item_does_not_truncate_it():
    """Fix-round-2 regression (task review, Important). A stray bare numeral
    inside the *last* item's own body — a page number, a cross-reference, an
    enumerated sub-point — must not be mistaken for the start of a new section
    under `item_style="punctuated"`. Every M3 source (MPRS, MPF, Cebraspe) uses
    this style; the questionário-de-percepção rationale for also checking `bare`
    is OAB-specific and must not run here, or an incidental digit drops the item
    outright instead of merely landing inside a choice (the lesser, pre-existing
    splice defect this must not regress into something worse)."""
    text = (
        "1. Primeira pergunta?\n(A) alfa1\n(B) beta1\n(C) gama1\n(D) delta1\n"
        "2. Segunda pergunta?\n(A) alfa2\n(B) beta2\n45\n(C) gama2\n(D) delta2\n"
    )
    items = segment_objetiva(text)
    assert len(items) == 2
    for it in items:
        assert len(it.choices) == 4


# Two guards stand between an incidental bare numeral and a truncated item: the
# `bare`/`questao` scoping above, and `_tail_boundary`'s four-choice floor. In the
# fixture above they overlap — its stray "45" is followed by only two choice lines,
# so the floor rejects it whether or not the scoping ran, and dropping *either*
# guard alone left the suite green (final whole-branch review, finding I3). The two
# tests below each remove that overlap from one side, so a future edit to either
# guard fails on its own.


def test_the_punctuated_scoping_holds_when_the_four_choice_floor_cannot_help():
    """Pins the `item_style in ("bare", "questao")` scoping alone.

    The stray numeral sits *before* the last item's four alternatives, so the
    four-choice floor sees a well-formed item and would accept it as a section
    start. Only the scoping keeps the OAB-specific `bare` tail check from running
    against a punctuated source and truncating item 2 to a bare stem.
    """
    text = (
        "1. Primeira pergunta?\n(A) alfa1\n(B) beta1\n(C) gama1\n(D) delta1\n"
        "2. Segunda pergunta, na forma do art. 45 do CPP?\n45\n"
        "(A) alfa2\n(B) beta2\n(C) gama2\n(D) delta2\n"
    )

    items = segment_objetiva(text)

    assert len(items) == 2
    assert [len(i.choices) for i in items] == [4, 4]
    assert items[-1].choices[-1]["text"] == "delta2"


def test_the_four_choice_floor_holds_where_the_scoping_does_not_apply():
    """Pins `_tail_boundary`'s floor alone.

    Under `item_style="bare"` the scoping is inert by construction — the bare tail
    pattern is checked whatever it decides — so the floor is the only thing left. A
    stray numeral trailed by two alternatives is an incidental digit inside the last
    item's body, not the OAB questionário restarting at 1, and must not bound it.
    """
    text = (
        "1\nPrimeira pergunta?\n(A) alfa1\n(B) beta1\n(C) gama1\n(D) delta1\n"
        "2\nSegunda pergunta?\n(A) alfa2\n(B) beta2\n45\n(C) gama2\n(D) delta2\n"
    )

    items = segment_objetiva(text, item_style="bare")

    assert len(items) == 2
    assert [len(i.choices) for i in items] == [4, 4]
    assert items[-1].choices[-1]["text"] == "delta2"


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


# ---- grids: banded (OAB 1a-fase, M2.5 Task 3, amendment E14) --------------
#
# Four heading spellings occur across the 19 real gabaritos, and the pattern that
# binds on the tipo token matches more than four times per file — the tabela de
# correspondência that follows Tipo 4 repeats the tipo tokens, including a
# "TIPO 1 TIPO 2 TIPO 3 TIPO 4" column header. So read_grid(style="banded") takes
# the *first* match for the requested tipo and scopes to the *next* heading of any
# tipo; raising on many matches (not just none) is the specific trap this guards.

EXAME43 = (OAB_FIX / "exame_43_gabarito.txt").read_text(encoding="utf-8")
EXAME29 = (OAB_FIX / "exame_29_gabarito.txt").read_text(encoding="utf-8")


def test_banded_tipo1_reads_all_80_answers_with_two_annulments():
    grid = read_grid(EXAME43, style="banded", tipo=1)
    assert len(grid) == 80
    assert set(grid) == set(range(1, 81))
    annulled = sorted(n for n, v in grid.items() if v is None)
    assert annulled == [1, 74]


def test_banded_scoping_each_tipo_has_its_own_annulled_set():
    """The scoping regression: a mis-scoped implementation returns the same block
    (or Tipo 4's, since it is scanned last) for every tipo. Each tipo's annulled
    pair is different in the fixture, so asserting all four proves real scoping."""
    expected = {1: [1, 74], 2: [4, 73], 3: [6, 71], 4: [2, 72]}
    for tipo, want in expected.items():
        grid = read_grid(EXAME43, style="banded", tipo=tipo)
        assert len(grid) == 80, f"tipo {tipo} did not recover all 80 answers"
        annulled = sorted(n for n, v in grid.items() if v is None)
        assert annulled == want, f"tipo {tipo} annulled set"


def test_banded_tipo1_and_tipo2_disagree_on_a_specific_item():
    """Not merely different annulments — different letters too, so a scope that
    reused Tipo 2's rows for a Tipo 1 request could not pass this."""
    tipo1 = read_grid(EXAME43, style="banded", tipo=1)
    tipo2 = read_grid(EXAME43, style="banded", tipo=2)
    assert tipo1[2] == "C"
    assert tipo2[2] == "A"
    assert tipo1[2] != tipo2[2]


def test_banded_tipo_none_raises():
    with pytest.raises(GridError):
        read_grid(EXAME43, style="banded")


def test_banded_tipo_that_does_not_occur_raises():
    with pytest.raises(GridError):
        read_grid(EXAME43, style="banded", tipo=9)


def test_banded_head_and_answer_row_length_mismatch_names_both_counts():
    text = "PROVA TIPO 1\n1 2 3 4 5\nA B C\n"
    with pytest.raises(GridError) as exc_info:
        read_grid(text, style="banded", tipo=1)
    message = str(exc_info.value)
    assert "5" in message
    assert "3" in message


def test_banded_non_contiguous_recovery_is_rejected():
    text = "PROVA TIPO 1\n1 2 3 4 10\nA B C D E\n"
    with pytest.raises(GridError):
        read_grid(text, style="banded", tipo=1)


def test_banded_a_stray_tipo_mention_does_not_silently_truncate_the_block():
    """Fix-round-1, Important finding A of the task-3 review. The ≤60-char guard
    alone requires only that the line be short; it says nothing about whether a
    new block of answers actually starts there. A retificado/errata note that
    happens to mention "prova tipo 1" in passing (a live possibility — the OAB
    does republish corrected gabaritos) is short enough to match `_TIPO_HEAD`,
    and because it sits *inside* the real Tipo 1 block, treating it as a scope
    boundary silently drops the block's tail (items 6-10 here) without raising
    — truncation removes a contiguous run, so the existing contiguity check
    does not catch it either. `_block_headings`/`_restarts_at_item_one` fix
    this: a match only counts as a boundary if the next genuine band pair it
    introduces restarts numbering at item 1, and the stray note here is
    followed by the valid pair "6 7 8 9 10" / "A B C D E", which does not."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n"
        "Nota: revisado conforme edital da prova tipo 1.\n"
        "6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    grid = read_grid(text, style="banded", tipo=1)
    assert grid == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "A", 7: "B", 8: "C", 9: "D", 10: "E"}
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid2 == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B", 7: "C", 8: "D", 9: "E", 10: "A"}


def test_banded_a_coincidental_head_row_with_no_valid_answer_does_not_end_the_block():
    """Fix-round-2, Important finding A (continued): fix-round-1's remedy checked
    only that the first band-head-shaped row after a candidate heading opened
    with "1", not that it paired with a genuine answer row beneath it. A stray
    heading-shaped mention can be immediately followed by an *unrelated*
    all-integer row that happens to start at 1 ("1 6 11 16 21") purely by
    coincidence, whose own next line ("P Q R S T") is not answer-shaped at all
    — round-1's check accepted this as "restarts at 1" and truncated the block
    anyway. The whole-file parse (`_tipo_blocks`, fix-round-3) only ever
    assembles a pair from a head row *and* a conforming answer row beneath it
    (`_band_pairs`), so the coincidental row never becomes a pair at all;
    scanning finds the real pair ("6 7 8 9 10" / "A B C D E") instead, which
    does not restart at 1 — the stray note is still correctly not a block
    start of its own, and Tipo 1 recovers all 10 items."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n"
        "Nota: revisado conforme edital da prova tipo 1.\n"
        "1 6 11 16 21\n"
        "P Q R S T\n"
        "6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    grid = read_grid(text, style="banded", tipo=1)
    assert grid == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "A", 7: "B", 8: "C", 9: "D", 10: "E"}
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid2 == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B", 7: "C", 8: "D", 9: "E", 10: "A"}


def test_banded_a_genuine_pair_split_by_page_furniture_still_starts_its_own_block():
    """Fix-round-3, Critical regression introduced by fix-round-2. Round 2's
    `_restarts_at_item_one` required a head row and its answer row to be the
    *very next* line, with nothing between them. A genuine Tipo 2 heading whose
    head row and answer row are split by one ordinary page-furniture line (a
    running head at a page break — real, since tipo headings sit at page
    breaks) then looked, from round 2's code, like it had no pair of its own
    at all; the boundary-detection scan walked straight past the real Tipo 2
    heading and found Tipo 1's *own trailing* pair instead, which is what
    caused this to reproduce as silent cross-tipo content contamination
    (count stays correct, content is wrong) rather than a raised error.
    `_band_pairs` now tolerates furniture between a head row and its answer
    row — the same tolerance the boundary check and the actual read share, so
    they cannot drift apart again — so Tipo 2's own pair is found correctly
    and it starts its own block."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\nPagina 2\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    grid1 = read_grid(text, style="banded", tipo=1)
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid1 == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "A", 7: "B", 8: "C", 9: "D", 10: "E"}
    assert len(grid2) == 10
    assert grid2 == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B", 7: "C", 8: "D", 9: "E", 10: "A"}


def test_banded_tipo1_never_contains_tipo2s_letters():
    """The lesson of the fix-round-3 regression, asserted directly: a scoping
    bug can leave the *entry count* looking correct while the *content* is
    wrong (Tipo 2's letters merged into Tipo 1's grid). Asserting only `len`
    would have passed on the regressed code, since it also returned 10 entries
    for Tipo 1 — just the wrong 10. This asserts on content: none of Tipo 1's
    recovered letters may equal what Tipo 2 actually holds at the same item
    unless the two tipos genuinely agree there, and at least one item where
    they must disagree (item 6, by construction of the fixture below) is
    checked explicitly."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\nPagina 2\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    grid1 = read_grid(text, style="banded", tipo=1)
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid1[6] == "A"
    assert grid2[6] == "B"
    assert grid1[6] != grid2[6]
    for item in range(1, 11):
        assert grid1[item] != grid2[item], f"tipo 1 item {item} leaked tipo 2's letter"


# ---- banded: a pair is recognised by its answer row, not by listing whatever
# ---- may sit between the two rows -----------------------------------------

# Two tipos, ten items each, disagreeing at every item — so a merge of one into
# the other is visible in the *content*, not only in the entry count. The
# fix-round-4 defect kept every count correct.
_TIPO1_TEN = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "A", 7: "B", 8: "C", 9: "D", 10: "E"}
_TIPO2_TEN = {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B", 7: "C", 8: "D", 9: "E", 10: "A"}


@pytest.mark.parametrize(
    "intruder",
    [
        "2",                                  # a bare page number
        "(*)Questão anulada",                 # the annulment legend — real corpus content
        "Errata: gabarito retificado",        # an errata note
        "|",                                  # whatever the next extraction invents
    ],
    ids=["bare-page-number", "annulment-legend", "errata-note", "extraction-artifact"],
)
def test_banded_an_unlisted_line_between_head_and_answer_rows_does_not_merge_two_tipos(
    intruder,
):
    """Fix-round-4, the Critical finding of the task-3 review. Fix-round-3 paired
    a head row with its answer row by *skipping the lines between them that are
    blank or match `_FURNITURE`* — that is, by enumerating what may sit there.
    `_FURNITURE` knows `Página N`, `N/M`, `PROVA OBJETIVA` and three MP running
    heads. A bare page number is not on that list; neither is a legend, a
    footnote, an errata note, or anything a future PDF extraction produces. Nor
    can any list ever close: three fix rounds were each defeated by an input
    nobody had enumerated.

    When the skip failed, a genuine tipo heading looked like it had no band pair
    of its own, `_tipo_blocks` demoted it from block start, and its bands merged
    into the *previous* tipo's block — so Tipo 2's answer key came back under a
    Tipo 1 request while Tipo 2 itself raised, with the entry count still
    perfectly correct (10 here, 80 in a real gabarito). `* Questão anulada` is
    not a hypothetical intruder: it is line 40 of `exame_43_gabarito.txt`.

    The rule is inverted instead — search a bounded distance forward for the
    next line that *is* a conforming `_BAND_ANSWER_ROW`. `A`-`E` plus
    `ANNULMENT_TOKENS` is a closed set; its complement is not."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        f"PROVA TIPO 2\n1 2 3 4 5\n{intruder}\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN
    assert read_grid(text, style="banded", tipo=2) == _TIPO2_TEN


def test_banded_a_merge_that_would_give_one_item_two_answers_raises():
    """The forward search for an answer row is bounded (`_BAND_ANSWER_LOOKAHEAD`),
    and this test spends that bound deliberately: six lines separate Tipo 2's
    head row from its answer row, so the pair is genuinely not found and Tipo 2
    is demoted exactly as in the fix-round-4 defect. What must not follow is the
    defect's *outcome* — a plausible ten-entry grid in which items 6-10 silently
    carry Tipo 2's letters. Within one tipo's block an item has exactly one
    answer, so the merge is detectable at the moment it corrupts, and the read
    refuses instead. This is the safety net that makes the bound's residual a
    refusal rather than a wrong answer key."""
    filler = "\n".join(f"linha {i}" for i in range(6))
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        f"PROVA TIPO 2\n1 2 3 4 5\n{filler}\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    with pytest.raises(GridError) as exc_info:
        read_grid(text, style="banded", tipo=1)
    assert "two different answers" in str(exc_info.value)
    with pytest.raises(GridError):
        read_grid(text, style="banded", tipo=2)


def test_banded_stacked_page_furniture_between_head_and_answer_rows_still_pairs():
    """A page break in a real gabarito emits several lines at once — the 29º's
    extraction carries four consecutive ones between two tipo blocks. All three
    below happen to be `_FURNITURE`-shaped, which is now beside the point: what
    finds the answer row is that it *is* an answer row."""
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\nPagina 2\n3 / 4\nPROVA OBJETIVA\n"
        "B C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN
    assert read_grid(text, style="banded", tipo=2) == _TIPO2_TEN


def test_banded_a_blank_line_between_head_and_answer_rows_still_pairs():
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
        "PROVA TIPO 2\n1 2 3 4 5\n\nB C D E A\n6 7 8 9 10\nB C D E A\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN
    assert read_grid(text, style="banded", tipo=2) == _TIPO2_TEN


# ---- banded: the structural cases the fix rounds must keep intact ----------


def test_banded_a_heading_immediately_followed_by_another_heading_raises_for_the_first():
    """The first heading has no band of its own, so it is not a block start and
    tipo 1 refuses — it must not instead inherit tipo 2's band."""
    text = "PROVA TIPO 1\nPROVA TIPO 2\n1 2 3 4 5\nB C D E A\n"
    with pytest.raises(GridError):
        read_grid(text, style="banded", tipo=1)
    assert read_grid(text, style="banded", tipo=2) == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A"}


def test_banded_a_tipo_whose_first_band_does_not_start_at_one_raises():
    """A block whose first band opens at 6 is a fragment, not a block: returning
    it would hand the caller a partial answer key with no sign that it is one."""
    with pytest.raises(GridError):
        read_grid("PROVA TIPO 1\n6 7 8 9 10\nA B C D E\n", style="banded", tipo=1)


def test_banded_a_repeated_same_tipo_heading_mid_block_merges_forward():
    """A running head repeating the tipo mid-block introduces no new block; its
    bands continue the one already open."""
    text = "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\nPROVA TIPO 1\n6 7 8 9 10\nA B C D E\n"
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN


def test_banded_a_band_pair_before_any_heading_is_dropped():
    """Bands above the first heading belong to no tipo, so they are dropped, not
    attributed to whichever tipo happens to be requested."""
    text = (
        "1 2 3 4 5\nX X X X X\n"
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN


def test_banded_a_heading_at_eof_with_no_bands_raises():
    text = (
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\nPROVA TIPO 2\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN
    with pytest.raises(GridError):
        read_grid(text, style="banded", tipo=2)


def test_banded_tipos_appearing_out_of_order_each_read_their_own_block():
    text = (
        "PROVA TIPO 2\n1 2 3 4 5\nB C D E A\n6 7 8 9 10\nB C D E A\n"
        "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\nA B C D E\n"
    )
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN
    assert read_grid(text, style="banded", tipo=2) == _TIPO2_TEN


def test_banded_an_all_annulled_answer_row_is_a_valid_band():
    """A row of nothing but `*` is a legitimate answer row (a whole band annulled
    on appeal), not a decorative separator, and yields Nones rather than raising."""
    text = "PROVA TIPO 1\n1 2 3 4 5\n* * * * *\n6 7 8 9 10\nA B C D E\n"
    assert read_grid(text, style="banded", tipo=1) == {
        1: None, 2: None, 3: None, 4: None, 5: None,
        6: "A", 7: "B", 8: "C", 9: "D", 10: "E",
    }


def test_banded_a_decorative_star_row_with_no_head_row_above_it_is_ignored():
    """The same row shape with no head row above it is not a band at all."""
    text = "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n* * * * *\n6 7 8 9 10\nA B C D E\n"
    assert read_grid(text, style="banded", tipo=1) == _TIPO1_TEN

# ---- banded: the second real spelling, plus the correspondência table trap -


def test_banded_endash_tipo_spelling_reads_all_four_tipos():
    """exame_29's heading is 'XXIX EXAME DE ORDEM UNIFICADO – TIPO n – COR' — a
    different spelling from the 43º's 'PROVA TIPO n', with en-dashes either side
    of the tipo token instead of the word PROVA."""
    for tipo in (1, 2, 3, 4):
        grid = read_grid(EXAME29, style="banded", tipo=tipo)
        assert len(grid) == 80
        assert set(grid) == set(range(1, 81))


def test_banded_endash_spelling_scoping_gives_each_tipo_its_own_content():
    """Fix-round-1, Important finding C of the task-3 review: exame_29 has zero
    annulments, so the annulled-set trick that catches a scoping bug in exame_43
    is unavailable here, and the previous version of this test asserted only
    counts and key ranges — a mutant that always reads Tipo 1's block regardless
    of the requested tipo passed it. Item 1 differs across all four tipos in this
    fixture (verified against the raw text), so asserting it here kills exactly
    that mutant: a "return Tipo 1 always" implementation would answer 'D' to
    every one of the four calls below, not the three distinct correct letters."""
    grid1 = read_grid(EXAME29, style="banded", tipo=1)
    grid2 = read_grid(EXAME29, style="banded", tipo=2)
    grid3 = read_grid(EXAME29, style="banded", tipo=3)
    grid4 = read_grid(EXAME29, style="banded", tipo=4)
    assert grid1[1] == "D"
    assert grid2[1] == "A"
    assert grid3[1] == "B"
    assert grid4[1] == "B"
    assert grid1[1] not in (grid2[1], grid3[1], grid4[1])


def test_banded_endash_spelling_tipo4_scope_ends_before_the_correspondencia_table():
    """exame_29 also carries the tabela de correspondência after Tipo 4, whose
    column header repeats 'TIPO 1 TIPO 2 TIPO 3 TIPO 4' and whose body is pure
    integer pairs. What actually protects Tipo 4's read here is `_TIPO_HEAD`
    matching that column header (it restarts band-head numbering at 1, so
    `_block_headings` accepts it) and ending Tipo 4's scope right there, before
    any table row is in scope at all — not `_BAND_ANSWER_ROW` (fix-round-1,
    Important finding B of the task-3 review: stubbing that guard out changes
    nothing here, or on any of the 19 real gabaritos, precisely because scope
    never reaches a table row in real data). This test pins that scoping
    behaviour, named for what it actually verifies; `_BAND_ANSWER_ROW`'s own
    necessity is pinned directly, on synthetic input, by
    `test_band_answer_row_guard_rejects_a_table_shaped_row_within_scope` below."""
    grid = read_grid(EXAME29, style="banded", tipo=4)
    assert len(grid) == 80
    assert set(grid) == set(range(1, 81))
    assert all(v is None or v in "ABCDE" for v in grid.values())


def test_band_answer_row_guard_rejects_a_table_shaped_row_within_scope():
    """Direct unit test for `_BAND_ANSWER_ROW`'s own necessity (fix-round-1,
    Important finding B): a pair of all-integer rows sitting inside a tipo's
    scope, with no intervening heading to end the scope first, must not be
    read as more answers. The chosen numbers (6-10) are deliberately
    contiguous with the real band's numbers (1-5): without the guard, `_verdict`
    would pass the bare digit strings "11".."15" through unchanged as if they
    were answer letters, and because 1-10 stays contiguous even with that
    garbage mixed in, the existing contiguity check would not catch it either
    — a silent corruption, not a raised GridError."""
    text = "PROVA TIPO 1\n1 2 3 4 5\nA B C D E\n6 7 8 9 10\n11 12 13 14 15\n"
    grid = read_grid(text, style="banded", tipo=1)
    assert grid == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
    assert 6 not in grid, "the table-shaped continuation must not be read as answers"


# ---- banded: heading spellings with no committed fixture (synthetic, per the
# ---- task brief — these test a regex against a heading *shape*, not exam
# ---- content, so a minimal inline string is legitimate here) --------------


def test_banded_prova_n_spelling_with_no_tipo_token():
    """39º-style: 'XXXIX EXAME DE ORDEM UNIFICADO - PROVA 1' has no 'TIPO' token
    at all — just PROVA directly followed by the number."""
    text = (
        "XXXIX EXAME DE ORDEM UNIFICADO - PROVA 1\n"
        "1 2 3 4 5 6\n"
        "A B C D E A\n"
        "XXXIX EXAME DE ORDEM UNIFICADO - PROVA 2\n"
        "1 2 3 4 5 6\n"
        "B C D E A B\n"
    )
    grid1 = read_grid(text, style="banded", tipo=1)
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid1 == {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "A"}
    assert grid2 == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B"}


def test_banded_bare_prova_tipo_spelling_with_no_exam_prefix():
    """44º-style: a bare 'PROVA TIPO 1' with no exam-number/ordinal prefix at all."""
    text = "PROVA TIPO 1\n1 2 3 4 5 6\n* B C D E A\nPROVA TIPO 2\n1 2 3 4 5 6\nB C D E A B\n"
    grid1 = read_grid(text, style="banded", tipo=1)
    grid2 = read_grid(text, style="banded", tipo=2)
    assert grid1 == {1: None, 2: "B", 3: "C", 4: "D", 5: "E", 6: "A"}
    assert grid2 == {1: "B", 2: "C", 3: "D", 4: "E", 5: "A", 6: "B"}


# ---- banded: regression — the other two styles are unchanged and still refuse -


def test_paired_rows_and_interleaved_are_unchanged_after_banded_is_added():
    grid = read_grid(PAIRED, style="paired_rows")
    assert len(grid) == 10 and grid[1] == "C" and grid[5] is None

    grid = read_grid(
        (FIX / "mprs_50_grid.txt").read_text(encoding="utf-8"), style="interleaved"
    )
    assert len(grid) == 100


def test_paired_rows_rejects_an_oab_gabarito():
    with pytest.raises(GridError):
        read_grid(EXAME43, style="paired_rows")


def test_interleaved_rejects_an_oab_gabarito():
    with pytest.raises(GridError):
        read_grid(EXAME43, style="interleaved")
