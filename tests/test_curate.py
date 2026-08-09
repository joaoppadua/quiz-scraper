import pytest

from bqpp.config import load_settings, load_taxonomy
from bqpp.curate import record_use, render_shortlist, run_curate, score
from bqpp.db import Database
from bqpp.models import Question, SourceDocument, VetReason


@pytest.fixture
def settings(tmp_path):
    """A settings copy whose shortlist_dir is disposable."""
    s = load_settings().model_copy(deep=True)
    s.shortlist_dir = tmp_path / "shortlists"
    return s


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    d.upsert_source_document(
        SourceDocument(id="d18", source_id="hf-oab-exams", url="u", fetched_at="t",
                       kind="dataset", banca="FGV", carreira="oab",
                       certame="OAB 2018-01", exam_year=2018)
    )
    d.upsert_source_document(
        SourceDocument(id="d10", source_id="hf-oab-exams", url="u", fetched_at="t",
                       kind="dataset", banca="FGV", carreira="oab",
                       certame="OAB 2010-01", exam_year=2010)
    )
    yield d
    d.close()


def _q(qid, doc="d18", fmt="mcq4", status="ok", rationale=None):
    return Question(
        id=qid, source_doc_id=doc, question_number=qid, format=fmt,
        stem=f"Enunciado {qid}", choices=[{"label": "A", "text": "a"}], answer_key="A",
        answer_rationale=rationale, discipline="direito-processual-penal",
        subtopic_ids=["T1.2"], vet_status=status, pedagogy_note="nota",
    )


def test_ranking_prefers_ok_open_format_recent_and_rationale(db, settings):
    r = settings.ranking
    d18 = db.get_source_document("d18")
    d10 = db.get_source_document("d10")
    assert score(_q("a", fmt="dissertativa"), d18, r) > score(_q("b", fmt="mcq4"), d18, r)
    assert score(_q("c", status="ok"), d18, r) > score(_q("d", status="flagged"), d18, r)
    assert score(_q("e", doc="d18"), d18, r) > score(_q("f", doc="d10"), d10, r)
    assert score(_q("g", rationale="gabarito"), d18, r) > score(_q("h"), d18, r)


def test_rejected_and_previously_used_are_excluded(db, settings):
    db.upsert_question(_q("keep"))
    db.upsert_question(_q("bad", status="rejected"))
    db.upsert_question(_q("used"))
    record_use(db, "used", "2025.2", "T1.2")
    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    text = (settings.shortlist_dir / "2026-2" / "T1.2.md").read_text(encoding="utf-8")
    assert "keep" in text and "bad" not in text and "used" not in text
    assert written["T1.2"] == 1


def test_current_semester_pick_stays_in_its_own_shortlist_and_is_marked(db, settings):
    """Recording a pick must not delete it from the shortlist it was picked from —
    the file is the professor's class-prep artifact, and a re-curate would otherwise
    silently swap the chosen question for the next-ranked one."""
    db.upsert_question(_q("chosen"))
    db.upsert_question(_q("other"))
    record_use(db, "chosen", "2026.2", "T1.2")

    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    text = (settings.shortlist_dir / "2026-2" / "T1.2.md").read_text(encoding="utf-8")
    assert written["T1.2"] == 2
    assert "chosen" in text
    assert "Já escolhida para 2026.2" in text

    # ...but it is gone from the next semester, which is the point of the log
    run_curate(db, load_taxonomy(), settings, semester="2027.1", subtopic_ids=["T1.2"])
    later = (settings.shortlist_dir / "2027-1" / "T1.2.md").read_text(encoding="utf-8")
    assert "chosen" not in later and "other" in later


def test_unvetted_questions_are_not_shortlisted(db, settings):
    db.upsert_question(_q("raw", status="unvetted"))
    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    assert written["T1.2"] == 0


def test_shortlist_is_self_contained_markdown(db, settings):
    q = _q("q1", fmt="dissertativa", rationale="Gabarito comentado da banca")
    q.vet_status = "flagged"
    q.vet_reasons = [VetReason(code="resposta_mudou_mas_util", detail="art. 316 CPP")]
    doc = db.get_source_document("d18")
    md = render_shortlist("T1.2", "Prisão preventiva", [(q, doc)], semester="2026.2")
    assert "Enunciado q1" in md  # verbatim stem
    assert "<details>" in md and "Gabarito comentado da banca" in md
    assert "FGV" in md and "OAB 2018-01" in md and "2018" in md
    assert "resposta_mudou_mas_util" in md
    assert "bqpp use q1 --semester 2026.2 --subtopic T1.2" in md
    assert "⚠" in md  # flagged banner


def test_shortlist_is_capped_at_the_configured_size(db, settings):
    for i in range(9):
        db.upsert_question(_q(f"q{i}"))
    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    assert written["T1.2"] == settings.ranking.shortlist_size == 5


def test_empty_subtopic_still_writes_a_file_saying_so(db, settings):
    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T3.3"])
    text = (settings.shortlist_dir / "2026-2" / "T3.3.md").read_text(encoding="utf-8")
    assert "Nenhum candidato" in text


def test_dry_run_writes_no_files(db, settings):
    db.upsert_question(_q("q1"))
    run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"], dry_run=True
    )
    assert not (settings.shortlist_dir / "2026-2").exists()


# ---- M3: shared context above the stem ------------------------------------

def test_context_is_rendered_above_the_stem():
    """A certo/errado item is a proposition hanging off a comando; without the
    comando above it, the item is unintelligible on its own."""
    from bqpp.curate import render_shortlist
    from bqpp.models import Question, SourceDocument

    q = Question(
        id="q1", source_doc_id="d1", question_number="87", format="certo_errado",
        stem_context="Acerca da prova no processo penal, julgue os itens a seguir.",
        stem="O afastamento da prova pericial pelo magistrado não enseja nulidade.",
        answer_key="E", answer_rationale="Errado. O livre convencimento motivado...",
        vet_status="ok",
    )
    doc = SourceDocument(id="d1", source_id="cebraspe-cadernos", url="https://cdn.cebraspe.org.br/x",
                         fetched_at="t", kind="gabarito_justificado", banca="CEBRASPE",
                         carreira="delegado", certame="PC/DF Delegado 2026", exam_year=2026)
    md = render_shortlist("T3.4", "Provas em espécie", [(q, doc)], semester="2026.2")

    assert "julgue os itens a seguir" in md
    assert md.index("julgue os itens a seguir") < md.index("O afastamento da prova pericial")
    assert "CEBRASPE" in md and "PC/DF Delegado 2026" in md


def test_a_question_without_context_renders_unchanged():
    from bqpp.curate import render_shortlist
    from bqpp.models import Question

    q = Question(id="q1", source_doc_id="d1", format="dissertativa", stem="Enunciado solto.",
                 vet_status="ok")
    md = render_shortlist("T1.1", "Princípios", [(q, None)], semester="2026.2")
    assert "Enunciado solto." in md
    assert "> _" not in md, "no empty context block"
