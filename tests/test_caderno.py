"""Cebraspe combined-caderno reader: item + answer + banca justificativa in one file."""

from pathlib import Path

import pytest

from bqpp.parse.caderno import MAX_COMANDO_CHARS, segment_caderno

FIX = Path(__file__).parent / "fixtures" / "cebraspe"


@pytest.fixture(scope="module")
def pc_df():
    return segment_caderno((FIX / "pc_df_26.txt").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dp_df():
    return segment_caderno((FIX / "dp_df_19.txt").read_text(encoding="utf-8"))


def test_every_item_has_an_answer_and_a_rationale(pc_df):
    assert pc_df
    for it in pc_df:
        assert it.answer_key in ("C", "E")
        assert it.rationale, f"item {it.number} lost its justificativa"
        assert it.stem


def test_answer_keys_come_from_the_justificativa_line(pc_df):
    verdicts = [it.answer_key for it in pc_df]
    assert verdicts.count("C") > 0 and verdicts.count("E") > 0


def test_the_2019_caderno_parses_without_the_sentinel(dp_df):
    """DP/DF 19 is the same genre but prints zero <FimJust> sentinels, while
    PC/DF 26 prints 105. Depending on the sentinel loses this whole era."""
    assert dp_df
    assert all(it.answer_key in ("C", "E") for it in dp_df)
    assert all(it.rationale for it in dp_df)


def test_the_sentinel_never_leaks_into_content(pc_df):
    for it in pc_df:
        assert "<FimJust>" not in it.stem
        assert "<FimJust>" not in it.rationale


def test_items_carry_the_comando_of_their_block(pc_df):
    with_context = [it for it in pc_df if it.comando]
    assert len(with_context) > len(pc_df) // 2, "most items hang off a comando"
    for it in with_context:
        assert "julgue" in it.comando.lower() or len(it.comando) > 40


def test_a_new_comando_starts_a_new_block(pc_df):
    comandos = [it.comando for it in pc_df if it.comando]
    assert len(set(comandos)) > 1, "the caderno has several comandos"


def test_sibling_items_share_one_comando(pc_df):
    from collections import Counter

    counts = Counter(it.comando for it in pc_df if it.comando)
    assert max(counts.values()) >= 2, "a comando governs several items"


# ---- the short-comando gate (the professor's scope decision) ---------------

def test_topic_sentence_comandos_are_usable():
    text = (
        "Julgue os seguintes itens, relativos ao direito processual penal.\n"
        "1 O inquérito policial é peça dispensável para a propositura da ação penal.\n"
        "JUSTIFICATIVA - Certo. Trata-se de peça informativa e dispensável.\n"
    )
    (item,) = segment_caderno(text)
    assert item.usable
    assert len(item.comando) < MAX_COMANDO_CHARS


def test_multi_paragraph_fact_patterns_are_rejected():
    """Items hanging off a long hypothetical read as fragments when lifted alone."""
    fact_pattern = (
        "Roberto, com arma de fogo em punho, abordou Mário, vigilante de um museu, tendo "
        "solicitado que ele abrisse a porta da sala principal. " * 6
    ) + "Com base nessa situação hipotética, julgue os itens a seguir."
    text = (
        f"{fact_pattern}\n"
        "1 O crime se consumou com a inversão da posse do bem.\n"
        "JUSTIFICATIVA - Certo. A jurisprudência consolidou esse entendimento.\n"
    )
    (item,) = segment_caderno(text)
    assert not item.usable, "fact-pattern items are excluded by the professor's decision"
    assert "situação hipotética" in item.comando.lower(), (
        "rejection comes from the comando pointing back at narrative the item does not carry"
    )


def test_a_rejected_item_still_keeps_its_text():
    """Rejection is a shortlist decision, not data loss — the caller logs and skips."""
    text = (
        "Mário foi preso em flagrante. A autoridade lavrou o auto. "
        "A partir dessa situação hipotética, julgue os itens a seguir.\n"
        "1 Item qualquer.\nJUSTIFICATIVA - Errado. Porque sim.\n"
    )
    (item,) = segment_caderno(text)
    assert not item.usable
    assert item.stem and item.rationale and item.comando


@pytest.mark.parametrize(
    "julgue",
    [
        "Com base nessa situação hipotética, julgue os itens a seguir.",
        "A partir do texto precedente, julgue os itens subsequentes.",
        "Tendo como referência essa situação hipotética, julgue os itens.",
        "Considerando a situação apresentada, julgue os itens seguintes.",
    ],
)
def test_every_refers_back_phrasing_is_caught(julgue):
    text = f"Narrativa qualquer. {julgue}\n1 Proposição.\nJUSTIFICATIVA - Certo. Porque sim.\n"
    (item,) = segment_caderno(text)
    assert not item.usable, f"{julgue!r} should have been rejected"


@pytest.mark.parametrize(
    "julgue",
    [
        "Julgue os seguintes itens, relativos ao direito processual penal.",
        "Acerca da prova no processo penal, julgue os itens a seguir.",
        "No que se refere às nulidades processuais, julgue os itens subsequentes.",
    ],
)
def test_topic_sentences_are_not_rejected(julgue):
    text = f"{julgue}\n1 Proposição.\nJUSTIFICATIVA - Certo. Porque sim.\n"
    (item,) = segment_caderno(text)
    assert item.usable, f"{julgue!r} should have been accepted"


def test_page_furniture_never_reaches_content(pc_df):
    for it in pc_df:
        body = f"{it.stem} {it.rationale} {it.comando or ''}"
        for junk in ("-- PROVA OBJETIVA --", "Edital:", "Espaço livre"):
            assert junk not in body, f"{junk!r} leaked into item {it.number}"


def test_empty_input_yields_nothing():
    assert segment_caderno("") == []
    assert segment_caderno("texto solto sem itens") == []
