"""The OAB exam-index scraper, against trimmed real pages. No network."""

from datetime import date
from pathlib import Path

import pytest

from bqpp.harvest.oab_site import (
    Exam,
    IndexEntry,
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
    assert select_penal_padrao([IndexEntry(href="x.pdf", label=label)]) is None


# ---- reaplicação variants (amendment B11) ---------------------------------

def test_reaplicacao_variants_are_kept_as_separate_entries(index_xxv):
    entries = parse_exam_index(index_xxv)
    penal = [e for e in entries if "Penal" in e.label and "Padrão" in e.label]
    assert len(penal) >= 2, "XXV published a Porto Alegre/RS reapplication"
    assert any(e.variant for e in penal), "the variant suffix must be captured, not dropped"


def test_variant_is_extracted_from_the_label():
    e = IndexEntry(
        href="x.pdf",
        label="14/09/2018 - Padrão de respostas definitivo (Direito Penal) - Porto Alegre/RS",
    )
    assert e.variant == "Porto Alegre/RS"


def test_a_plain_label_has_no_variant():
    assert IndexEntry(href="x.pdf", label="11/11/2025 - Padrão de respostas (Direito Penal)").variant is None


# ---- exam_year comes from the label, never the URL (amendment B4) ---------

def test_exam_year_comes_from_the_label_not_the_url_path(index_xxv):
    """Pre-2019 files were rehomed under /arquivos/2019/10/, so the path lies."""
    entry, _ = select_penal_padrao(parse_exam_index(index_xxv))
    assert entry.date is not None
    assert entry.date.year == 2018, "XXV was applied in 2018"
    assert "/2019/" in entry.href, "...even though the URL says 2019 (this is the point)"


# ============================ ingestion ====================================

import json  # noqa: E402

from bqpp.db import Database  # noqa: E402
from bqpp.harvest.oab_site import ingest_padrao  # noqa: E402
from bqpp.models import Question, SourceDocument, question_id  # noqa: E402
from bqpp.parse.padrao import segment_padrao  # noqa: E402
from bqpp.parse.pdf import extract_text  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def padrao_44_text():
    return extract_text(FIX / "padrao_44.pdf")


def _ingest(db, text, *, label="44º EXAME DE ORDEM UNIFICADO", entry=None, rung="definitivo",
            force=False):
    entry = entry or IndexEntry(
        href="https://s.oab.org.br/arquivos/2025/11/b9ef8314.pdf",
        label="11/11/2025 - Padrão de respostas definitivo (Direito Penal)",
    )
    return ingest_padrao(
        text, source_id="oab-2f-penal", exam=Exam(id="17000", label=label), entry=entry,
        rung=rung, banca="FGV", carreira="oab", db=db, force=force,
    )


def test_ingest_writes_one_question_per_usable_section(db, padrao_44_text):
    assert _ingest(db, padrao_44_text) == 5
    assert {q.format for q in db.iter_questions()} == {"peca", "dissertativa"}


def test_ingested_questions_carry_the_bancas_rationale(db, padrao_44_text):
    _ingest(db, padrao_44_text)
    for q in db.iter_questions():
        assert q.answer_rationale, "the gabarito comentado is the point of this milestone"
        assert q.answer_key is None, "discursive questions have no letter key"


def test_one_source_document_per_exam_with_the_year_from_the_label(db, padrao_44_text):
    _ingest(db, padrao_44_text)
    docs = [db.get_source_document(q.source_doc_id) for q in db.iter_questions()]
    assert len({d.id for d in docs}) == 1
    doc = docs[0]
    assert doc.kind == "gabarito_justificado"
    assert doc.banca == "FGV" and doc.carreira == "oab"
    assert doc.exam_year == 2025, "exam_year drives the law watchlist; it must be set"
    assert "44º" in doc.certame and "2ª fase" in doc.certame


def test_reaplicacao_variant_reaches_the_certame(db, padrao_44_text):
    entry = IndexEntry(
        href="https://s.oab.org.br/x.pdf",
        label="14/09/2018 - Padrão de respostas definitivo (Direito Penal) - Porto Alegre/RS",
    )
    _ingest(db, padrao_44_text, label="XXV EXAME DE ORDEM UNIFICADO", entry=entry)
    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert "Porto Alegre/RS" in doc.certame


def test_unusable_sections_are_skipped_not_stored_empty(db):
    """A section whose enunciado did not extract must not become a question.

    Uses a synthetic rationale-only padrão: the VII fixture no longer qualifies, since
    its `Enunciado:` sub-headers are recognised now that the anchors tolerate a colon.
    """
    text = "PADRÃO DE RESPOSTA – QUESTÃO 1\nGabarito Comentado\n" + "Resposta esperada. " * 30
    assert _ingest(db, text, label="VII EXAME DE ORDEM UNIFICADO") == 0
    assert list(db.iter_questions()) == []


def test_the_pre_2013_era_now_ingests(db):
    """VII writes `Enunciado:` / `Gabarito comentado:`; the whole era was being dropped."""
    text = (FIX / "padrao_vii.txt").read_text(encoding="utf-8")
    assert _ingest(db, text, label="VII EXAME DE ORDEM UNIFICADO") == 5
    for q in db.iter_questions():
        assert q.stem and q.answer_rationale


def test_ingest_is_idempotent(db, padrao_44_text):
    assert _ingest(db, padrao_44_text) == 5
    assert _ingest(db, padrao_44_text) == 0
    assert len(list(db.iter_questions())) == 5


def test_force_rewrites_existing_rows(db, padrao_44_text):
    _ingest(db, padrao_44_text)
    assert _ingest(db, padrao_44_text, force=True) == 5
    assert len(list(db.iter_questions())) == 5


def test_a_question_already_held_from_another_source_is_not_duplicated(db, padrao_44_text):
    """Exams 39º-44º are already in the corpus from maritaca-ai/oab-bench."""

    other = SourceDocument(id="bench", source_id="hf-oab-bench", url="https://hf.co/x",
                           fetched_at="t", kind="dataset", banca="FGV", carreira="oab")
    db.upsert_source_document(other)
    first = segment_padrao(padrao_44_text)[1]
    db.upsert_question(Question(id=question_id("bench", "44_q1"), source_doc_id="bench",
                                question_number="44_q1", format="dissertativa", stem=first.stem))

    assert _ingest(db, padrao_44_text) == 4, "the stem already in the corpus is skipped"
    assert len(list(db.iter_questions())) == 5


def test_dedup_survives_reflowed_whitespace(db, padrao_44_text):

    other = SourceDocument(id="bench", source_id="hf-oab-bench", url="https://hf.co/x",
                           fetched_at="t", kind="dataset")
    db.upsert_source_document(other)
    stem = segment_padrao(padrao_44_text)[1].stem
    mangled = "  ".join(stem.upper().split())          # different case, different wrapping
    db.upsert_question(Question(id=question_id("bench", "x"), source_doc_id="bench",
                                question_number="x", format="dissertativa", stem=mangled))
    assert _ingest(db, padrao_44_text) == 4


def test_provenance_records_which_rung_of_the_ladder_was_used(db, padrao_44_text):
    _ingest(db, padrao_44_text, rung="plain")
    q = next(db.iter_questions())
    assert "preliminar" in (q.classified_note or "").lower() or q.classified_note is None
    doc = db.get_source_document(q.source_doc_id)
    assert doc.url.startswith("https://")


def test_question_numbers_are_peca_and_digits(db, padrao_44_text):
    _ingest(db, padrao_44_text)
    numbers = sorted(q.question_number for q in db.iter_questions())
    assert numbers == ["1", "2", "3", "4", "peca"]


def test_stems_are_stored_verbatim(db, padrao_44_text):

    _ingest(db, padrao_44_text)
    stems = {q.stem for q in db.iter_questions()}
    assert {s.stem for s in segment_padrao(padrao_44_text)} == stems
    assert json.dumps(list(stems))  # round-trips as JSON for the export


def test_every_application_variant_is_selected(index_xxv):
    """XXV was applied twice. The index is newest-first and the reaplicação is
    published last, so picking one best entry harvested Porto Alegre/RS and never
    requested the main application at all (amendment B11 says do not collapse them)."""
    from bqpp.harvest.oab_site import select_penal_padroes

    chosen = select_penal_padroes(parse_exam_index(index_xxv))
    assert {e.variant for e, _ in chosen} == {None, "Porto Alegre/RS"}
    assert all(rung == "definitivo" for _, rung in chosen)


def test_the_main_application_comes_first(index_xxv):
    from bqpp.harvest.oab_site import select_penal_padroes

    chosen = select_penal_padroes(parse_exam_index(index_xxv))
    assert chosen[0][0].variant is None
    assert select_penal_padrao(parse_exam_index(index_xxv))[0].variant is None


def test_a_single_application_yields_exactly_one(index_44):
    from bqpp.harvest.oab_site import select_penal_padroes

    chosen = select_penal_padroes(parse_exam_index(index_44))
    assert len(chosen) == 1 and chosen[0][1] == "definitivo"
