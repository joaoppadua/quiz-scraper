"""Pure selection logic for the OAB 1ª-fase objective exam (M2.5, Task 5). No network.

Artifact-selection labels are real: `entries_44` re-reads the same committed,
already-trimmed index page `test_oab_site.py` uses, and the standalone `IndexEntry`
literals below were copied verbatim out of the (gitignored) `data/raw/oab` cache
rather than invented — the cache itself cannot be read at test time because it is
not checked in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bqpp.harvest.oab_1f import Artifacts, is_criminal, select_1f_artifacts
from bqpp.harvest.oab_site import IndexEntry, parse_exam_index
from bqpp.parse.objetiva import ObjetivaItem

FIX = Path(__file__).parent / "fixtures" / "oab_site"


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
