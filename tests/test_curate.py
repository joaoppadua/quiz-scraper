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
    # T1.3, not T3.3: the latter is now marked `opens_with: doutrina` and is
    # deliberately not reported as a coverage gap.
    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.3"])
    text = (settings.shortlist_dir / "2026-2" / "T1.3.md").read_text(encoding="utf-8")
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


# ---- fix: certo/errado response space + unambiguous verdict -------------------


def _mcq4_with_answer_c(qid):
    """An mcq4 whose gabarito letter happens to collide with the certo/errado
    verdict letter 'C' — the exact collision the professor hit in T1.3.md."""
    return Question(
        id=qid, source_doc_id="d1", question_number=qid, format="mcq4",
        stem="Assinale a alternativa correta.",
        choices=[
            {"label": "A", "text": "primeira"},
            {"label": "B", "text": "segunda"},
            {"label": "C", "text": "terceira"},
            {"label": "D", "text": "quarta"},
        ],
        answer_key="C", vet_status="ok",
    )


def test_certo_errado_renders_the_response_space_and_verdict_certo():
    q = Question(
        id="q1", source_doc_id="d1", question_number="2", format="certo_errado",
        stem="O flagrante forjado por policiais afasta a legalidade da prisão.",
        answer_key="C", answer_rationale="Certo. ...", vet_status="ok",
    )
    md = render_shortlist("T3.4", "Provas em espécie", [(q, None)], semester="2026.2")
    assert "- **C)** Certo" in md
    assert "- **E)** Errado" in md
    assert "**Resposta:** CERTO" in md


def test_certo_errado_renders_verdict_errado():
    q = Question(
        id="q1", source_doc_id="d1", question_number="3", format="certo_errado",
        stem="A prisão preventiva prescinde de fundamentação concreta.",
        answer_key="E", vet_status="ok",
    )
    md = render_shortlist("T3.4", "Provas em espécie", [(q, None)], semester="2026.2")
    assert "**Resposta:** ERRADO" in md


def test_mcq4_alternatives_and_bare_answer_letter_are_unchanged():
    q = _mcq4_with_answer_c("m1")
    md = render_shortlist("T1.2", "x", [(q, None)], semester="2026.2")
    assert "- **A)** primeira" in md
    assert "- **C)** terceira" in md
    assert "**Resposta:** C" in md
    answer_lines = [line for line in md.splitlines() if line.startswith("**Resposta:**")]
    assert answer_lines == ["**Resposta:** C"]


def test_dissertativa_and_peca_render_without_an_options_block():
    for fmt in ("dissertativa", "peca"):
        q = Question(id=f"q-{fmt}", source_doc_id="d1", format=fmt, stem="Enunciado.",
                     vet_status="ok")
        md = render_shortlist("T1.1", "x", [(q, None)], semester="2026.2")
        assert "- **" not in md, f"{fmt} must not render an options block"
        assert "**Resposta:** — (discursiva)" in md


def test_an_annulled_objective_item_says_so_instead_of_discursiva():
    """A null answer key means two different things. A question the banca annulled is
    classroom material, kept on purpose (spec §10.2) — labelling it "(discursiva)"
    describes the wrong kind of item. Unreachable through `curate` today because vet
    rejects `nullified` rows, but it is the same ambiguity the certo/errado fix
    closed one line above."""
    q = Question(id="anulada1", source_doc_id="d1", question_number="74", format="mcq4",
                 stem="Enunciado.", choices=[{"label": "A", "text": "primeira"}],
                 answer_key=None, nullified=True, vet_status="ok")

    md = render_shortlist("T1.1", "x", [(q, None)], semester="2026.2")

    assert "**Resposta:** — (anulada)" in md
    assert "discursiva" not in md


def test_certo_errado_and_mcq4_answer_lines_are_not_confusable_in_one_document():
    """The professor's exact bug: entry 2 (certo_errado, answer_key='C') and entry 3
    (mcq4, answer_key='C') rendered the identical string '**Resposta:** C'."""
    ce = Question(id="ce1", source_doc_id="d1", question_number="2", format="certo_errado",
                  stem="Proposição.", answer_key="C", vet_status="ok")
    mc = _mcq4_with_answer_c("mc1")
    md = render_shortlist("T1.3", "x", [(ce, None), (mc, None)], semester="2026.2")
    answer_lines = [line for line in md.splitlines() if line.startswith("**Resposta:**")]
    assert answer_lines == ["**Resposta:** CERTO", "**Resposta:** C"]
    assert len(set(answer_lines)) == 2, "the two answer lines must not be identical strings"


def test_a_doctrine_subtopic_says_so_instead_of_reporting_a_gap(tmp_path):
    from bqpp.config import load_settings, load_taxonomy
    from bqpp.curate import run_curate
    from bqpp.db import Database

    settings = load_settings().model_copy(deep=True)
    settings.shortlist_dir = tmp_path / "shortlists"
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()

    written = run_curate(db, load_taxonomy(), settings, semester="2026.2",
                         subtopic_ids=["T3.3"])
    md = (settings.shortlist_dir / "2026-2" / "T3.3.md").read_text(encoding="utf-8")
    assert "doutrina" in md.lower()
    assert "Amplie o corpus" not in md, "this is not a coverage gap"
    assert "T3.3" not in [k for k, v in written.items() if v == 0 and k != "T3.3"]
    db.close()


def test_an_ordinary_empty_subtopic_still_reports_a_gap(tmp_path):
    from bqpp.config import load_settings, load_taxonomy
    from bqpp.curate import run_curate
    from bqpp.db import Database

    settings = load_settings().model_copy(deep=True)
    settings.shortlist_dir = tmp_path / "shortlists"
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.3"])
    md = (settings.shortlist_dir / "2026-2" / "T1.3.md").read_text(encoding="utf-8")
    assert "Nenhum candidato" in md
    db.close()
