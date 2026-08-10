"""The OAB 1ª-fase adapter (M2.5, Tasks 5 and 6). No network, no LLM, no PDFs.

Artifact-selection labels are real: `entries_44` re-reads the same committed,
already-trimmed index page `test_oab_site.py` uses, and the standalone `IndexEntry`
literals below were copied verbatim out of the (gitignored) `data/raw/oab` cache
rather than invented — the cache itself cannot be read at test time because it is
not checked in.

The ingestion half is driven from `tests/fixtures/oab_1f/`, which holds
pdfplumber-extracted **text** of real cadernos and gabaritos. No exam PDF is
committed (this repo is public), and `harvest_source` is exercised against a stub
transport, so the whole file runs offline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest

from bqpp.harvest.oab_1f import (
    Artifacts,
    choose_item_style,
    harvest_source,
    ingest_caderno,
    is_criminal,
    read_tipo_grid,
    select_1f_artifacts,
)
from bqpp.harvest.oab_site import Exam, IndexEntry, parse_exam_index
from bqpp.parse.objetiva import GridError, ObjetivaItem

FIX = Path(__file__).parent / "fixtures" / "oab_site"
FIX_1F = Path(__file__).parent / "fixtures" / "oab_1f"


@pytest.fixture
def entries_44() -> list[IndexEntry]:
    """The 44º Exame's real index page.

    Carries a genuine Tipo 1..4 caderno set, a Gabaritos Definitivos, the 2ª-fase
    "Caderno de Provas"/"Padrão de respostas" distractors, and the 1ª-fase
    Edital/Resultado administrative distractors — everything amendment E7 needs to
    exercise, on one real page.
    """
    html = (FIX / "exam_index_44.html").read_text(encoding="utf-8")
    return parse_exam_index(html)


def _only(entries: list[IndexEntry], *needles: str) -> IndexEntry:
    """The single entry whose label contains every needle; fails loudly otherwise.

    Locating the real fixture entries this way (rather than retyping their labels)
    sidesteps transcription mistakes with the en dash the OAB uses inconsistently
    ("Tipo 1" vs "Tipo – 2").
    """
    matches = [e for e in entries if all(n in e.label for n in needles)]
    assert len(matches) == 1, f"expected exactly one match for {needles}, got {matches}"
    return matches[0]


# ---- real IndexEntry literals, copied from the (gitignored) data/raw/oab cache ----

# 09/02/2020 exam: a Tipo 1..4 caderno set and a single preliminar gabarito — this
# exam never had a definitivo published.
_TIPO_1_2020 = IndexEntry(
    href="http://s.oab.org.br/arquivos/2020/02/5cffeded-9393-4c6d-b8f8-a4fdd44058ac.pdf",
    label="09/02/2020 - Caderno de Prova - Tipo 1",
)
_GABARITO_PRELIMINAR_2020 = IndexEntry(
    href="http://s.oab.org.br/arquivos/2020/02/d25ed53f-a3cb-4b86-a76f-ba7c7caa15d3.pdf",
    label="09/02/2020 - Gabaritos Preliminares da Prova Objetiva (1ª fase)",
)
PRELIMINAR_ONLY = [_TIPO_1_2020, _GABARITO_PRELIMINAR_2020]

# 2016 exam: two preliminar gabaritos at different dates — the OAB republishing a
# corrected key under the same label family (the "- atualizado" variants the
# cached pages also carry are the same pattern under a different suffix).
_GABARITO_PRELIMINAR_2016_OLDER = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/5747e52b-29d0-42a6-90a3-3473f920b3a4.pdf",
    label="24/07/2016 - Gabaritos Preliminares da Prova Objetiva (1ª fase)",
)
_GABARITO_PRELIMINAR_2016_NEWER = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/30f7e349-9129-4aab-ab32-debaec47b7a8.pdf",
    label="14/08/2016 - Gabaritos Preliminares da Prova Objetiva (1ª fase) - "
    "Examinandos de Salvador/BA",
)
SEVERAL_PRELIMINARES = [
    _TIPO_1_2020,  # any Tipo 1 caderno completes the pair; the date mismatch is fine
    _GABARITO_PRELIMINAR_2016_OLDER,
    _GABARITO_PRELIMINAR_2016_NEWER,
]

# The exact E7 distractor: a "Resultado Definitivo (após recursos)" label, real,
# from a 15/01/2013 exam.
RESULTADO_DEFINITIVO = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/505665e3-1f44-487e-96ca-4f12c4770701.pdf",
    label="15/01/2013 - Resultado Definitivo (após recursos) - Prova Objetiva (1ª fase)",
)

# The exact 2ª-fase and edital distractors from E7's amendment text, real, from a
# 2025 exam's index page.
CADERNO_PENAL_2A_FASE = IndexEntry(
    href="https://s.oab.org.br/arquivos/2025/06/1eb5df53-7170-4765-91f6-6e4f2b0357a9.pdf",
    label="15/06/2025 - Caderno de Provas (Direito Penal)",
)
CADERNO_CIVIL_2A_FASE = IndexEntry(
    href="https://s.oab.org.br/arquivos/2025/06/1ff5d82a-674b-41f3-9782-819e2e6e716b.pdf",
    label="15/06/2025 - Caderno de Provas (Direito Civil)",
)
PADRAO_PENAL_2A_FASE = IndexEntry(
    href="https://s.oab.org.br/arquivos/2025/06/8a847f3d-ed48-4795-bd2e-01b9497699fb.pdf",
    label="15/06/2025 - Padrão de respostas (Direito Penal)",
)
EDITAL_LOCAIS_HORARIO = IndexEntry(
    href="https://s.oab.org.br/arquivos/2025/04/2d412a0f-0ece-4bf6-912b-bdd7862de2c9.pdf",
    label="16/04/2025 - Edital - Locais e Horário de Realização da Prova Objetiva (1ª fase)",
)

# Exam 11553 (2016): a genuine second administration on one page — a reaplicação in
# Salvador/BA, each with its own Tipo 1..4 caderno set and its own gabarito. Fix for
# review Finding 1: picking `cadernos[0]` and ranking gabaritos independently can
# pair one administration's questions with the other's answer key. All four real
# labels below, verbatim (note this exam's own lowercase "Caderno de prova").
CADERNO_TIPO_1_2016_MAIN = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/08fce9f6-4722-4756-819e-58fadde35b66.pdf",
    label="24/07/2016 - Caderno de prova - Tipo 1",
)
CADERNO_TIPO_1_2016_REAPLICACAO = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/7614a970-ec8b-4e26-80eb-14a4aae49ea6.pdf",
    label="14/08/2016 - Caderno de prova - Tipo 1 (Reaplicação Salvador/BA)",
)
GABARITO_2016_MAIN = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/5747e52b-29d0-42a6-90a3-3473f920b3a4.pdf",
    label="24/07/2016 - Gabaritos Preliminares da Prova Objetiva (1ª fase)",
)
GABARITO_2016_REAPLICACAO = IndexEntry(
    href="http://s.oab.org.br/arquivos/2019/10/30f7e349-9129-4aab-ab32-debaec47b7a8.pdf",
    label="14/08/2016 - Gabaritos Preliminares da Prova Objetiva (1ª fase) - "
    "Examinandos de Salvador/BA",
)
# Real document order (index pages are newest-first, so the reaplicação — published
# three weeks after the main application — is listed first).
EXAM_11553_TIPO_1 = [
    CADERNO_TIPO_1_2016_REAPLICACAO,
    GABARITO_2016_REAPLICACAO,
    CADERNO_TIPO_1_2016_MAIN,
    GABARITO_2016_MAIN,
]

# Synthetic (dated 2027, engineered — not from the cache) distractors for Finding 2:
# labels that carry an `_ADMIN` word but are built to still satisfy `_CADERNO_TIPO`
# and `_GABARITO`, so the guard's necessity is actually exercised rather than
# assumed. Across all 46 real cached pages neither pattern ever needed the `_ADMIN`
# filter to reject a real label — these two are what a future label wording could
# look like if it did.
ADVERSARIAL_ADMIN_CADERNO = IndexEntry(
    href="https://s.oab.org.br/arquivos/2027/01/adversarial-caderno.pdf",
    label="01/01/2027 - Edital - Caderno de Prova - Tipo 1",
)
ADVERSARIAL_ADMIN_GABARITO = IndexEntry(
    href="https://s.oab.org.br/arquivos/2027/01/adversarial-gabarito.pdf",
    label="01/01/2027 - Comunicado - Gabaritos Preliminares - Prova Objetiva (1ª fase)",
)


# ------------------------------------------------------------ artifact selection ---


def test_definitivo_preferred_for_tipo_1(entries_44):
    expected_caderno = _only(entries_44, "Caderno de Prova", "Tipo 1")
    expected_gabarito = _only(entries_44, "Gabaritos Definitivos")

    art = select_1f_artifacts(entries_44)

    assert art == Artifacts(caderno=expected_caderno, gabarito=expected_gabarito, definitivo=True)


def test_falls_back_to_preliminar_when_no_definitivo():
    art = select_1f_artifacts(PRELIMINAR_ONLY)

    assert art == Artifacts(
        caderno=_TIPO_1_2020, gabarito=_GABARITO_PRELIMINAR_2020, definitivo=False
    )


def test_newest_preliminar_wins_among_several():
    art = select_1f_artifacts(SEVERAL_PRELIMINARES)

    assert art is not None
    assert art.gabarito == _GABARITO_PRELIMINAR_2016_NEWER
    assert art.definitivo is False


def test_tipo_3_selects_tipo_3_not_tipo_1(entries_44):
    expected_caderno = _only(entries_44, "Caderno de Prova", "Tipo 3")

    art = select_1f_artifacts(entries_44, tipo=3)

    assert art is not None
    assert art.caderno == expected_caderno
    assert art.caderno != _only(entries_44, "Caderno de Prova", "Tipo 1")


def test_none_when_caderno_missing():
    entries = [_GABARITO_PRELIMINAR_2020]

    assert select_1f_artifacts(entries) is None


def test_none_when_gabarito_missing():
    entries = [_TIPO_1_2020]

    assert select_1f_artifacts(entries) is None


def test_2a_fase_distractors_are_never_selected(entries_44):
    art = select_1f_artifacts(entries_44)

    assert art is not None
    penal_2a = _only(entries_44, "Caderno de Provas (Direito Penal)")
    civil_2a = _only(entries_44, "Caderno de Provas (Direito Civil)")
    padrao_2a = _only(entries_44, "Padrão de respostas (Direito Penal)")
    assert art.caderno not in (penal_2a, civil_2a)
    assert art.gabarito != padrao_2a


def test_1a_fase_edital_and_resultado_distractors_are_never_selected(entries_44):
    art = select_1f_artifacts(entries_44)

    assert art is not None
    edital = _only(entries_44, "Edital - Locais e Horário de Realização da Prova Objetiva")
    resultado = _only(entries_44, "Resultado Definitivo - Prova Objetiva")
    assert art.gabarito not in (edital, resultado)


def test_resultado_definitivo_apos_recursos_distractor_never_wins():
    entries = [*PRELIMINAR_ONLY, RESULTADO_DEFINITIVO]

    art = select_1f_artifacts(entries)

    assert art is not None
    assert art.gabarito == _GABARITO_PRELIMINAR_2020


def test_only_2a_fase_and_admin_material_yields_none():
    entries = [
        CADERNO_PENAL_2A_FASE,
        CADERNO_CIVIL_2A_FASE,
        PADRAO_PENAL_2A_FASE,
        EDITAL_LOCAIS_HORARIO,
        RESULTADO_DEFINITIVO,
    ]

    assert select_1f_artifacts(entries) is None


def test_multiple_administrations_on_one_page_refuse_rather_than_mispair():
    """Review Finding 1, pinned against exam 11553's real labels, in the real,
    cached document order (newest-first, so the Salvador/BA reaplicação — three
    weeks after the main application — is listed before it).

    Two Tipo-1 cadernos and two gabaritos, one pair per administration. Refusing
    is the right call regardless of whether the two old independent rules
    (`cadernos[0]`, and gabaritos ranked by `(definitivo, date)`) happen to agree
    on *this* snapshot — see the next test for why they can't be trusted to.
    """
    assert select_1f_artifacts(EXAM_11553_TIPO_1) is None


def test_the_old_independent_rules_could_disagree_and_silently_mispair():
    """Why refusing on ambiguity is necessary, not just tidy (Finding 1).

    On the real 11553 snapshot the two old rules — document-order `cadernos[0]`
    and gabarito ranked by `(definitivo, date)` — happen to agree (both pick the
    reaplicação: it's newest-first in the list, and it's also the newer of two
    otherwise-equal preliminares). That agreement is coincidence, not guarantee,
    and this test manufactures the disagreement to prove it: if the *main*
    administration's gabarito reaches definitivo first — an ordinary event, it is
    what happened on 16 of the other 45 cached pages — `rank()` jumps to it
    regardless of date, while `cadernos[0]` is unmoved and still returns whichever
    caderno the page happens to list first (the reaplicação's, here). The result
    is the main administration's questions paired with a different
    administration's answer key, wrong from question 1.

    Reimplements the pre-fix rule locally (`_ADMIN`/`_CADERNO_TIPO`/`_GABARITO`/
    `_DEFINITIVO`/`_fold` are the same private patterns `select_1f_artifacts`
    uses) rather than calling it, because the guard now lives inside that
    function and refuses before this pairing is ever computed — which is exactly
    the behaviour under test.
    """
    from bqpp.harvest.oab_1f import _ADMIN, _CADERNO_TIPO, _DEFINITIVO, _GABARITO, _fold

    main_gabarito_definitivo = IndexEntry(
        href=GABARITO_2016_MAIN.href,
        label="24/07/2016 - Gabaritos Definitivos - Prova Objetiva (1ª fase)",  # hypothetical
    )
    entries = [
        CADERNO_TIPO_1_2016_REAPLICACAO,
        GABARITO_2016_REAPLICACAO,
        CADERNO_TIPO_1_2016_MAIN,
        main_gabarito_definitivo,
    ]

    real = [e for e in entries if not _ADMIN.search(_fold(e.label))]
    old_caderno_pick = next(
        e for e in real if (m := _CADERNO_TIPO.search(_fold(e.label))) and int(m.group(1)) == 1
    )
    old_gabarito_pick = max(
        (e for e in real if _GABARITO.search(_fold(e.label))),
        key=lambda e: (
            bool(_DEFINITIVO.search(_fold(e.label))),
            e.date.isoformat() if e.date else "",
        ),
    )

    assert old_caderno_pick == CADERNO_TIPO_1_2016_REAPLICACAO
    assert old_gabarito_pick == main_gabarito_definitivo
    # The mispairing the fix exists to prevent: a reaplicação caderno with the
    # main administration's key.
    assert old_caderno_pick != CADERNO_TIPO_1_2016_MAIN
    assert old_gabarito_pick.href != GABARITO_2016_REAPLICACAO.href

    # The fixed function refuses this exact ambiguous page rather than picking
    # either side of that mismatch.
    assert select_1f_artifacts(entries, tipo=1) is None


def test_admin_filter_rejects_labels_engineered_to_defeat_the_artifact_patterns():
    """Review Finding 2: `_ADMIN` must earn its place with a test that fails without
    it. `ADVERSARIAL_ADMIN_CADERNO`/`_GABARITO` are synthetic labels built so that,
    absent the `_ADMIN` filter, they alone would satisfy `_CADERNO_TIPO` and
    `_GABARITO` and this would return a (wrong) `Artifacts` rather than `None`.
    """
    entries = [ADVERSARIAL_ADMIN_CADERNO, ADVERSARIAL_ADMIN_GABARITO]

    assert select_1f_artifacts(entries) is None


def test_gabarito_ranking_tolerates_a_missing_date():
    """Review Finding 3: an `IndexEntry` whose label carries no leading date parses
    to `.date is None`; ranking must still resolve deterministically rather than
    raising, and a dated entry outranks an undated one.
    """
    undated = IndexEntry(
        href="https://s.oab.org.br/arquivos/undated-gabarito.pdf",
        label="Gabaritos Preliminares da Prova Objetiva (1ª fase)",
    )
    entries = [_TIPO_1_2020, undated, _GABARITO_PRELIMINAR_2020]

    art = select_1f_artifacts(entries)

    assert art is not None
    assert art.gabarito == _GABARITO_PRELIMINAR_2020


# ---------------------------------------------------------------- the keyword gate ---

# Mirrors scripts/recon_1f.py's KEYWORDS_SEED + KEYWORDS_ADDED verbatim — the
# professor-approved, 16/16-recall list (E9). oab_1f.py must not hardcode it (it
# becomes config/sources.yaml in Task 6); tests use the real list rather than a
# hand-picked stand-in so a passing suite means something about the actual gate.
KEYWORDS = [
    "CPP",
    "processo penal",
    "inquérito",
    "flagrante",
    "preventiva",
    "temporária",
    "audiência de custódia",
    "denúncia",
    "queixa-crime",
    "resposta à acusação",
    "absolvição sumária",
    "nulidade",
    "prova ilícita",
    "interceptação",
    "habeas corpus",
    "júri",
    "pronúncia",
    "apelação",
    "recurso em sentido estrito",
    "competência",
    "Ministério Público",
    "delegado",
    "réu",
    "acusado",
    "sentença penal",
    "execução penal",
    "código penal",
    "crime",
    "criminal",
    "delito",
    "penal",
    "pena",
    "dosimetria",
    "prescrição da pretensão punitiva",
    "tipicidade",
    "dolo",
    "culposo",
    "legítima defesa",
    "furto",
    "roubo",
    "homicídio",
    "estelionato",
    "tráfico",
    "lei de drogas",
    "condenação",
    "condenado",
    "absolvição",
    "prisão",
    "delegacia",
    "autoridade policial",
    "vítima",
    "ofendido",
    "querelante",
    "querelado",
    "indiciado",
    "investigado",
    "defensor",
    "defensoria pública",
    "juizado especial criminal",
    "transação penal",
    "suspensão condicional",
    "acordo de não persecução",
    "boletim de ocorrência",
    "reincidente",
    "regime fechado",
    "regime semiaberto",
    "regime aberto",
    "livramento condicional",
    "delação",
    "colaboração premiada",
    "busca e apreensão",
    "mandado de prisão",
    "testemunha",
    "interrogatório",
    "instrução criminal",
    "trânsito em julgado",
    "recurso especial",
    "revisão criminal",
    "maria da penha",
    "violência doméstica",
    "improbidade",
    "domicílio",
    "inviolabilidade",
    "busca domiciliar",
    "mandado judicial",
    "ilícito",
    "polícia",
    "policial",
    "tipo penal",
    "culpabilidade",
    "imputável",
    "antijurídico",
    "tribunal do júri",
]


def _item(number: str, stem: str, choice_texts: list[str]) -> ObjetivaItem:
    return ObjetivaItem(
        number=number,
        stem=stem,
        choices=[{"label": chr(65 + i), "text": t} for i, t in enumerate(choice_texts)],
    )


def test_signal_only_in_the_stem_is_kept():
    item = _item(
        "1",
        "No processo penal brasileiro, o réu tem direito ao contraditório desde o inquérito.",
        ["Certo.", "Errado.", "Depende do juízo.", "Nenhuma das anteriores."],
    )

    assert is_criminal(item, KEYWORDS, min_hits=2)


def test_signal_only_in_an_alternative_is_kept_but_a_stem_only_search_fails():
    item = _item(
        "2",
        "Assinale a alternativa correta a respeito do tema abordado.",
        [
            "A prescrição tributária segue regras do CTN.",
            "O réu foi denunciado pela prática de furto qualificado.",
            "O contrato de locação rege-se pelo Código Civil.",
            "A responsabilidade civil objetiva independe de culpa.",
        ],
    )

    assert is_criminal(item, KEYWORDS, min_hits=2)

    # E9's core claim: scoring the stem alone must miss this item, because the
    # topic is declared only in one alternative, never in the stem itself.
    stem_only = ObjetivaItem(number=item.number, stem=item.stem, choices=[])
    assert not is_criminal(stem_only, KEYWORDS, min_hits=2)


def test_a_tributario_item_is_dropped():
    item = _item(
        "3",
        "Sobre o ICMS incidente na circulação de mercadorias, é correto afirmar que:",
        [
            "A alíquota interestadual é fixada por resolução do Senado Federal.",
            "O fato gerador ocorre na saída da mercadoria do estabelecimento.",
            "A base de cálculo exclui o próprio imposto.",
            "A substituição tributária progressiva é constitucional.",
        ],
    )

    assert not is_criminal(item, KEYWORDS, min_hits=2)


def test_min_hits_respected_a_single_incidental_reu_does_not_qualify():
    item = _item(
        "4",
        "O réu, na qualidade de parte no processo civil, requereu a produção de "
        "provas periciais e a citação por edital, nos termos do Código de Processo "
        "Civil.",
        [
            "A citação por edital exige esgotamento das tentativas de localização.",
            "A prova pericial é produzida por perito nomeado pelo juízo.",
            "A revelia gera presunção relativa de veracidade dos fatos.",
            "O julgamento antecipado dispensa a fase instrutória quando cabível.",
        ],
    )

    assert not is_criminal(item, KEYWORDS, min_hits=2)
    assert is_criminal(item, KEYWORDS, min_hits=1)


def test_juridico_does_not_match_juri_and_apenas_does_not_match_pena():
    item = _item(
        "5",
        "O parecer jurídico conclui que, apenas nesse caso, cabe a interposição de "
        "recurso hierárquico.",
        [
            "A decisão é discricionária quanto à conveniência e oportunidade.",
            "O ato produz efeitos a partir de sua publicação oficial.",
            "A hierarquia administrativa vincula os órgãos subordinados.",
            "O prazo para manifestação é de dez dias úteis.",
        ],
    )

    assert not is_criminal(item, KEYWORDS, min_hits=1)


# ======================================= Task 6: ingestion, and the wiring around it ==

# Everything below drives the *shipped* `oab-1f-penal` entry rather than a stand-in.
# The furniture list, the keyword list and the anchor candidates are the deliverable;
# a suite that passed against a hand-picked copy of them would say nothing about it.


@pytest.fixture(scope="module")
def source_entry():
    from bqpp.harvest.registry import load_sources

    return next(e for e in load_sources() if e.id == "oab-1f-penal")


@pytest.fixture
def params(source_entry) -> dict:
    return dict(source_entry.params)


@pytest.fixture
def db(tmp_path):
    from bqpp.db import Database

    database = Database.connect(tmp_path / "t.sqlite")
    database.init_schema()
    yield database
    database.close()


def _fixture_text(name: str) -> str:
    return (FIX_1F / f"{name}.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def caderno_43() -> str:
    """43º Exame, Tipo 1. Trimmed to questions 55-74 plus the trailing questionário."""
    return _fixture_text("exame_43_tipo1")


@pytest.fixture(scope="module")
def gabarito_43() -> str:
    """43º Exame: all four tipos' bands and the correspondence table. Definitivo."""
    return _fixture_text("exame_43_gabarito")


@pytest.fixture(scope="module")
def caderno_29() -> str:
    """XXIX Exame, Tipo 1 — the 2019 layout, which anchors on "Questão N"."""
    return _fixture_text("exame_29_tipo1")


@pytest.fixture(scope="module")
def gabarito_29() -> str:
    """XXIX Exame. Preliminares — no definitivo was ever published for it."""
    return _fixture_text("exame_29_gabarito")


@pytest.fixture(scope="module")
def caderno_42() -> str:
    """42º Exame, Tipo 1. Questions 40-48 — empresarial and civil, no criminal item."""
    return _fixture_text("exame_42_tipo1")


EXAM_43 = Exam(id="16773", label="43º EXAME DE ORDEM UNIFICADO")
EXAM_29 = Exam(id="11562", label="XXIX EXAME DE ORDEM UNIFICADO")


def _artifacts(*, definitivo: bool, applied: str = "27/04/2025") -> Artifacts:
    """A caderno/gabarito pair as `select_1f_artifacts` returns it.

    The dates are the real application dates of the two fixture exams, because
    `exam_year` is read off the caderno entry's label and nothing else.
    """
    rung = "Gabaritos Definitivos" if definitivo else "Gabaritos Preliminares"
    return Artifacts(
        caderno=IndexEntry(
            href="https://s.oab.org.br/arquivos/2025/04/caderno-tipo-1.pdf",
            label=f"{applied} - Caderno de Prova - Tipo 1",
        ),
        gabarito=IndexEntry(
            href="https://s.oab.org.br/arquivos/2025/05/gabaritos.pdf",
            label=f"{applied} - {rung} - Prova Objetiva (1ª fase)",
        ),
        definitivo=definitivo,
    )


ARTIFACTS_43 = _artifacts(definitivo=True)
ARTIFACTS_29 = _artifacts(definitivo=False, applied="30/06/2019")


# ---------------------------------------------------------------- the shipped entry ---


def test_the_adapter_is_registered_and_offline_capable():
    """`bqpp harvest` must reach it, and `bqpp parse` must be able to re-run it
    from the cache without asking the OAB for 38 PDFs again."""
    from bqpp.cli import _OFFLINE_CAPABLE, _adapters

    assert _adapters()["oab_1f"].__module__ == "bqpp.harvest.oab_1f"
    assert "oab_1f" in _OFFLINE_CAPABLE


def test_the_shipped_entry_carries_the_professor_approved_keyword_list(params):
    """The list is a domain decision and lives in config, never in code.

    `scripts/recon_1f.py` ships it with one accidental duplicate ("delegacia"); the
    config carries it de-duplicated and otherwise verbatim, in order.
    """
    assert params["keep_keywords"] == KEYWORDS
    assert len(params["keep_keywords"]) == len(set(params["keep_keywords"])) == 93
    assert params["min_keyword_hits"] == 2


def test_the_shipped_entry_configures_the_source_rather_than_the_code(params):
    assert params["tipo"] == 1
    assert params["min_exam_year"] == 2019
    assert params["columns"] == 2
    assert params["banca"] == "FGV" and params["carreira"] == "oab"
    assert params["item_style"] == ["bare", "questao"]
    # 13415's text layer is unmapped glyphs and only 65 of its 80 items come back.
    assert params["exclude_exam_ids"] == ["13415"]
    assert float(params["min_interval_seconds"]) >= 1.0   # spec §6: <= 1 req/s


# ------------------------------------------------------------------- the item anchor ---


def test_the_item_anchor_is_detected_per_caderno(caderno_43, caderno_29, params):
    """17 of 19 exams number with a bare numeral; the 2019 pair writes "Questão N".

    One configured value cannot cover both, so the source configures *candidates*
    and the adapter picks per caderno.
    """
    style_43, items_43 = choose_item_style(
        caderno_43, styles=params["item_style"], furniture=params["furniture"]
    )
    style_29, items_29 = choose_item_style(
        caderno_29, styles=params["item_style"], furniture=params["furniture"]
    )

    assert style_43 == "bare"
    assert [i.number for i in items_43] == [str(n) for n in range(55, 75)]
    assert style_29 == "questao"
    assert [i.number for i in items_29] == [str(n) for n in range(60, 69)]


def test_the_losing_anchor_is_not_merely_smaller_but_wrong(caderno_29, params):
    """The margin is what makes the detection safe, so it is pinned.

    Under `bare` the 2019 caderno recovers three unrelated fragments, not a prova.
    """
    style, items = choose_item_style(caderno_29, styles=["bare"], furniture=params["furniture"])

    assert style == "bare"
    assert len(items) < 5


def test_a_caderno_that_recovers_nothing_yields_nothing(params):
    style, items = choose_item_style(
        "nada aqui é uma questão\n", styles=params["item_style"], furniture=params["furniture"]
    )

    assert items == [] and style == ""


# ------------------------------------------------------------------ the furniture splice ---

# The defect this closes: the OAB prints a running head, a page number and a trailing
# "questionário de percepção" inside the question area, and the extracted text splices
# them into the last alternative of whichever item they land after. Choice *counts*
# stay right, so nothing bogus ships as an alternative — but a legal alternative ends
# with "A OAB e a FGV agradecem sua colaboração.", which is a straight violation of
# "reproduced verbatim, never paraphrased".

_SPLICE_MARKERS = (
    "A OAB e a FGV agradecem",
    "Questionário de percepção",
    "QUESTIONÁRIO DE PERCEPÇÃO",
    "Este questionário é de preenchimento facultativo",
    "Assinale suas respostas nos espaços próprios",
    "PROVA APLICAD",
    "EXAME DE ORDEM UN",
    "EXAME DO ORDEM UNIFICADO",
    "Tipo Branca",
    "TIPO 1 – BRANCA",
    "Página",
)


def _all_text(items) -> str:
    return "\n".join(i.stem + "\n" + "\n".join(c["text"] for c in i.choices) for i in items)


@pytest.mark.parametrize("fixture", ["exame_43_tipo1", "exame_29_tipo1", "exame_42_tipo1"])
def test_no_furniture_survives_into_a_question(fixture, params):
    text = _fixture_text(fixture)

    _, items = choose_item_style(
        text, styles=params["item_style"], furniture=params["furniture"]
    )

    assert items
    blob = _all_text(items)
    for marker in _SPLICE_MARKERS:
        assert marker not in blob, f"{fixture}: {marker!r} is spliced into a question"
    # The page number is furniture too, and it lands mid-alternative on 2019-2023
    # exams as a bare numeral of its own.
    assert not [ln for ln in blob.splitlines() if ln.strip().isdigit()]


def test_the_splice_is_real_without_the_furniture_list(caderno_43, params):
    """The guard above only means something if the defect exists unguarded."""
    _, unguarded = choose_item_style(caderno_43, styles=params["item_style"])

    assert "A OAB e a FGV agradecem sua colaboração." in _all_text(unguarded)


def test_stripping_furniture_removes_no_legal_text(caderno_43, params):
    """Same items, same alternatives, same wording — only the foreign lines go."""
    _, before = choose_item_style(caderno_43, styles=params["item_style"])
    _, after = choose_item_style(
        caderno_43, styles=params["item_style"], furniture=params["furniture"]
    )

    assert [i.number for i in before] == [i.number for i in after]
    for old, new in zip(before, after, strict=True):
        assert len(old.choices) == len(new.choices) == 4
        for a, b in zip(old.choices, new.choices, strict=True):
            assert a["label"] == b["label"]
            # every surviving line is a line that was already there, verbatim
            assert set(b["text"].splitlines()) <= set(a["text"].splitlines())
    assert after[-1].choices[-1]["text"].endswith(
        "sejam, férias vencidas e saldo de salários."
    )


# ---------------------------------------------------------------- the cross-tipo check ---


def test_the_four_tipos_are_read_and_agree(gabarito_43):
    grid = read_tipo_grid(gabarito_43, tipo=1)

    assert len(grid) == 80
    assert grid[2] == "C"
    assert grid[1] is None and grid[74] is None      # the 43º annuls two items


def test_a_tipo_block_short_of_a_band_is_refused(gabarito_43):
    """Entry count. A dropped trailing band cannot be seen from inside one block —
    60 contiguous answers look like a 60-question exam — but four blocks that
    disagree on the exam's length are unambiguous."""
    maimed = gabarito_43.replace(
        "61 62 63 64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80\n"
        "D C D B A D A B B D C B A * D C C A D A\n",
        "",
        1,
    )
    assert maimed != gabarito_43

    with pytest.raises(GridError, match="different entry counts"):
        read_tipo_grid(maimed, tipo=1)


def test_two_tipos_that_do_not_diverge_are_refused(gabarito_43):
    """Content divergence. A merge keeps the count right and corrupts the letters,
    so the count axis alone cannot see it. The four tipos are the same 80 questions
    in four shuffled orders: two that answer every item identically are one tipo
    read twice."""
    head_1 = "43º EXAME DE ORDEM - PROVA TIPO 1"
    head_2 = "43º EXAME DE ORDEM - PROVA TIPO 2"
    head_3 = "43º EXAME DE ORDEM - PROVA TIPO 3"
    block_1 = gabarito_43[gabarito_43.index(head_1) : gabarito_43.index(head_2)]
    cloned = (
        gabarito_43[: gabarito_43.index(head_2)]
        + block_1.replace(head_1, head_2)
        + gabarito_43[gabarito_43.index(head_3) :]
    )

    with pytest.raises(GridError, match="identically"):
        read_tipo_grid(cloned, tipo=1)


def test_the_real_tipos_diverge_far_from_the_threshold(gabarito_43, gabarito_29):
    """Measured across all 19 gabaritos, tipo 1 and tipo 2 differ on 41 to 70 of 80."""
    for gabarito in (gabarito_43, gabarito_29):
        one = read_tipo_grid(gabarito, tipo=1)
        two = read_tipo_grid(gabarito, tipo=2)
        assert sum(1 for n in one if one[n] != two[n]) >= 40


# ------------------------------------------------------------------------- ingestion ---


def _ingest_43(db, params, caderno, gabarito, **kw) -> int:
    return ingest_caderno(
        caderno, gabarito, exam=EXAM_43, artifacts=ARTIFACTS_43,
        source_id="oab-1f-penal", db=db, params=params, **kw,
    )


def test_one_source_document_per_exam_with_full_attribution(db, params, caderno_43, gabarito_43):
    """Spec §15: attribution is what makes classroom reproduction defensible."""
    _ingest_43(db, params, caderno_43, gabarito_43)

    docs = {q.source_doc_id for q in db.iter_questions()}
    assert len(docs) == 1
    doc = db.get_source_document(docs.pop())
    assert doc.kind == "prova"
    assert doc.banca == "FGV" and doc.carreira == "oab"
    assert doc.certame == "OAB 43º Exame (1ª fase)"
    assert doc.url == ARTIFACTS_43.caderno.href
    assert doc.source_id == "oab-1f-penal"


def test_exam_year_reaches_the_source_document(db, params, caderno_43, gabarito_43):
    """Load-bearing: the law watchlist only fires on questions whose year predates a
    change, so a null year silently disables vetting for all 80 questions."""
    _ingest_43(db, params, caderno_43, gabarito_43)

    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert doc.exam_year == 2025


def test_only_gate_passing_items_are_written(db, params, caderno_43, gabarito_43):
    """20 items in the fixture; the criminal/processo-penal block plus two borderline
    items pass. The count is exact, not a floor: a gate that let everything through
    would be indistinguishable from full coverage."""
    written = _ingest_43(db, params, caderno_43, gabarito_43)

    assert written == 14
    assert sorted(int(q.question_number) for q in db.iter_questions()) == [
        55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 70
    ]


def test_a_caderno_with_no_criminal_material_writes_nothing(db, params, caderno_42, gabarito_43):
    """The 42º's questions 40-48 are empresarial and civil. Nothing is written — and
    no orphan source_documents row is left behind either."""
    written = ingest_caderno(
        caderno_42, gabarito_43, exam=EXAM_43, artifacts=ARTIFACTS_43,
        source_id="oab-1f-penal", db=db, params=params,
    )

    assert written == 0
    assert list(db.iter_questions()) == []
    assert db.get_source_document(
        __import__("bqpp.models", fromlist=["x"]).source_doc_id(b"oab-1f-penal:16773:tipo1")
    ) is None


def test_every_question_is_mcq4_and_keyed_from_the_tipo_1_grid(
    db, params, caderno_43, gabarito_43
):
    _ingest_43(db, params, caderno_43, gabarito_43)
    grid = read_tipo_grid(gabarito_43, tipo=1)

    for q in db.iter_questions():
        assert q.format == "mcq4"
        assert len(q.choices) == 4
        assert [c["label"] for c in q.choices] == ["A", "B", "C", "D"]
        assert q.answer_key == grid[int(q.question_number)]
        assert q.answer_key in ("A", "B", "C", "D")
        assert q.stem


def test_a_definitivo_gabarito_leaves_the_key_settled(db, params, caderno_43, gabarito_43):
    _ingest_43(db, params, caderno_43, gabarito_43)

    assert all(not q.answer_key_provisional for q in db.iter_questions())


def test_a_preliminary_gabarito_marks_every_key_provisional(
    db, params, caderno_29, gabarito_29
):
    """The XXIX never published a definitivo; its key is still open to recursos."""
    written = ingest_caderno(
        caderno_29, gabarito_29, exam=EXAM_29, artifacts=ARTIFACTS_29,
        source_id="oab-1f-penal", db=db, params=params,
    )

    assert written == 9
    assert all(q.answer_key_provisional for q in db.iter_questions())
    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert doc.certame == "OAB XXIX Exame (1ª fase)"
    assert doc.exam_year == 2019


def test_an_annulled_item_is_flagged_not_dropped(db, params, caderno_43, gabarito_43):
    """A question the banca annulled is classroom material, not a defect. It is kept
    with a null key and `nullified` set.

    The gate is opened for this one test (`min_keyword_hits: 0`) because the 43º's
    annulled item in range is a trabalhista question the criminal gate rightly drops;
    the annulment path itself is what is under test.
    """
    params["min_keyword_hits"] = 0

    _ingest_43(db, params, caderno_43, gabarito_43)

    nullified = [q for q in db.iter_questions() if q.nullified]
    assert [q.question_number for q in nullified] == ["74"]
    assert nullified[0].answer_key is None
    assert nullified[0].stem


def test_reingesting_writes_nothing(db, params, caderno_43, gabarito_43):
    first = _ingest_43(db, params, caderno_43, gabarito_43)

    assert _ingest_43(db, params, caderno_43, gabarito_43) == 0
    assert len(list(db.iter_questions())) == first


def test_force_rewrites_without_duplicating(db, params, caderno_43, gabarito_43):
    first = _ingest_43(db, params, caderno_43, gabarito_43)

    assert _ingest_43(db, params, caderno_43, gabarito_43, force=True) == first
    assert len(list(db.iter_questions())) == first


def test_a_gabarito_whose_grid_cannot_be_read_writes_nothing(db, params, caderno_43):
    """Never default an answer key: a wrong key reaches a student as fact."""
    written = _ingest_43(db, params, caderno_43, "não há gabarito nenhum aqui.\n")

    assert written == 0
    assert list(db.iter_questions()) == []


# 5 items whose text layer is entirely unmapped glyphs — the state a plain emptiness
# check waves through and the corpus then ingests as noise.
GARBLED = "\n".join(
    f"{n}\n(cid:{n}0)(cid:{n}1)(cid:{n}2) (cid:{n}3)(cid:{n}4)\n"
    f"(A) (cid:{n}5)(cid:{n}6)\n(B) (cid:{n}7)(cid:{n}8)\n"
    f"(C) (cid:{n}9)(cid:{n}1)\n(D) (cid:{n}2)(cid:{n}3)"
    for n in range(1, 6)
)


def test_an_illegible_caderno_writes_nothing_and_does_not_abort(db, params, gabarito_43):
    params["min_keyword_hits"] = 0     # prove the health check, not the gate, is what drops them

    written = _ingest_43(db, params, GARBLED, gabarito_43)

    assert written == 0
    assert list(db.iter_questions()) == []


def test_document_level_glyph_noise_never_vetoes_a_legible_exam(
    db, params, caderno_43, gabarito_43
):
    """E11. Two real exams (12895, 13817) carry an unmapped *cover page* and 80
    individually clean questions. Judging the document as a whole discards both, so
    health is judged per item and the document-level call is a warning only."""
    from bqpp.parse.pdf import text_health

    noisy = "(cid:9)" * 900 + "\n" + caderno_43
    assert text_health(noisy) != "ok"

    written = _ingest_43(db, params, noisy, gabarito_43)

    assert written == 14


def test_an_item_absent_from_the_grid_is_skipped_not_keyed(db, params, caderno_43, gabarito_43):
    """The 43º fixture holds items 55-74; a 60-question exam keys five of them.

    All four tipos lose their last band, so this exercises the per-item "no entry in
    the grid" path rather than the cross-tipo check — which the previous two tests
    already own, and which fires first when only one tipo is short.
    """
    truncated = re.sub(
        r"^61 62 .*\n[A-E* ]+\n", "", gabarito_43, flags=re.M
    )
    assert len(read_tipo_grid(truncated, tipo=1)) == 60

    written = _ingest_43(db, params, caderno_43, truncated)

    assert written == 5
    assert max(int(q.question_number) for q in db.iter_questions()) <= 60


# ---------------------------------------------------------------------- harvest_source ---


class _StubResult:
    def __init__(self, body: bytes) -> None:
        self.body = body


class _StubFetcher:
    """Stands in for `harvest.http.Fetcher` so the orchestration can be driven offline.

    The real `Fetcher` is the only thing in the project that opens a socket and has
    its own tests; what needs proving here is that this adapter asks it for the right
    URLs, in the right order, with etiquette and dry-run honoured.
    """

    made: ClassVar[list] = []
    routes: ClassVar[dict[str, bytes]] = {}

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.requested: list[str] = []
        _StubFetcher.made.append(self)

    def get(self, url: str) -> _StubResult:
        from bqpp.harvest.http import FetchError

        self.requested.append(url)
        if url not in self.routes:
            raise FetchError(f"{url}: not routed")
        return _StubResult(self.routes[url])


@pytest.fixture
def harvest_rig(monkeypatch, source_entry, caderno_43, gabarito_43):
    """Routes the shipped URLs to the committed fixtures. Only exam 17000 resolves;
    every other exam id 404s, which is also the FetchError path under test."""
    from bqpp.harvest import oab_1f

    p = source_entry.params
    index_html = (FIX / "exam_index_44.html").read_text(encoding="utf-8")
    artifacts = select_1f_artifacts(parse_exam_index(index_html))
    routes = {
        p["seed_url"]: (FIX / "seed_page.html").read_bytes(),
        p["exam_url_template"].format(exam_id="17000"): index_html.encode("utf-8"),
        artifacts.caderno.href: b"CADERNO-PDF",
        artifacts.gabarito.href: b"GABARITO-PDF",
    }
    texts = {b"CADERNO-PDF": caderno_43, b"GABARITO-PDF": gabarito_43}

    _StubFetcher.made = []
    _StubFetcher.routes = routes
    monkeypatch.setattr(oab_1f, "Fetcher", _StubFetcher)
    monkeypatch.setattr(
        oab_1f, "extract_columns", lambda body, *, columns=2: texts[body]
    )
    return source_entry


@pytest.fixture
def settings_for_harvest(tmp_path):
    from bqpp.config import load_settings

    s = load_settings().model_copy(deep=True)
    s.data_dir = tmp_path
    return s


def test_harvest_walks_the_index_and_ingests(harvest_rig, settings_for_harvest, db):
    written = harvest_source(harvest_rig, db, settings_for_harvest)

    assert written == 14
    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert doc.certame == "OAB 44º Exame (1ª fase)"     # exam 17000's own label
    assert doc.exam_year == 2025                        # 17/08/2025, off the index entry
    assert doc.banca == "FGV"


def test_a_dead_exam_page_costs_that_exam_and_nothing_else(harvest_rig, settings_for_harvest, db):
    """46 of the 47 exam ids raise FetchError in the rig, and the run still finishes."""
    assert harvest_source(harvest_rig, db, settings_for_harvest) == 14

    fetcher = _StubFetcher.made[-1]
    assert len(fetcher.requested) > 40


def test_exams_before_min_exam_year_are_skipped(harvest_rig, settings_for_harvest, db):
    harvest_rig.params["min_exam_year"] = 2026

    assert harvest_source(harvest_rig, db, settings_for_harvest) == 0
    assert list(db.iter_questions()) == []
    assert not any(r.endswith(".pdf") for r in _StubFetcher.made[-1].requested)


def test_an_excluded_exam_is_never_requested(harvest_rig, settings_for_harvest, db):
    harvest_rig.params["exclude_exam_ids"] = ["17000", "13415"]

    assert harvest_source(harvest_rig, db, settings_for_harvest) == 0
    requested = _StubFetcher.made[-1].requested
    assert not any("NumeroExame=17000" in r for r in requested)


def test_dry_run_writes_nothing_and_opens_no_socket(harvest_rig, settings_for_harvest, db):
    written = harvest_source(harvest_rig, db, settings_for_harvest, dry_run=True)

    assert written == 0
    assert list(db.iter_questions()) == []
    fetcher = _StubFetcher.made[-1]
    # offline=True is what makes the real Fetcher refuse to leave the cache, and
    # db=None is what keeps a dry run out of the provenance manifest.
    assert fetcher.kwargs["offline"] is True
    assert fetcher.kwargs["db"] is None
    assert not any(r.endswith(".pdf") for r in fetcher.requested)


def test_harvest_etiquette_is_configured_from_settings(harvest_rig, settings_for_harvest, db):
    harvest_source(harvest_rig, db, settings_for_harvest)

    kwargs = _StubFetcher.made[-1].kwargs
    assert kwargs["user_agent"] == settings_for_harvest.harvest.user_agent
    assert kwargs["min_interval"] >= 1.0
    assert kwargs["cache_dir"] == settings_for_harvest.raw_dir / "oab_1f"


def test_harvest_is_idempotent(harvest_rig, settings_for_harvest, db):
    first = harvest_source(harvest_rig, db, settings_for_harvest)

    assert harvest_source(harvest_rig, db, settings_for_harvest) == 0
    assert len(list(db.iter_questions())) == first
