"""harvest (fixtures) -> classify -> vet -> curate, with a fake LLM. No network."""

import json
from pathlib import Path

from bqpp.classify import run_classify
from bqpp.config import load_settings, load_taxonomy
from bqpp.curate import record_use, run_curate
from bqpp.db import Database
from bqpp.export import export_jsonl
from bqpp.harvest.hf_datasets import ingest_oab_exams
from bqpp.llm.client import LLMClient
from bqpp.llm.fake_client import FakeBackend
from bqpp.models import SourceDocument
from bqpp.vet import load_watchlist, run_vet

FIX = Path(__file__).parent / "fixtures"


def _router(call):
    """One fake backend serving both stages, keyed on which prompt arrived."""
    if call["tier"] == "fast":
        return json.dumps(
            {
                "discipline": "direito-processual-penal",
                "subtopic_ids": ["T1.2"],
                "difficulty": "medium",
                "note": "",
            }
        )
    return json.dumps(
        {
            "verdict": "flagged",
            "reasons": [
                {
                    "code": "resposta_mudou_mas_util",
                    "detail": "Pacote Anticrime alterou o art. 316 do CPP",
                }
            ],
            "pedagogy_note": "Ótima para discutir a mudança legislativa em sala.",
        }
    )


def _pipeline(tmp_path):
    settings = load_settings().model_copy(deep=True)
    settings.shortlist_dir = tmp_path / "shortlists"
    settings.export_dir = tmp_path / "export"

    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    bundle = SourceDocument(
        id="bundle", source_id="hf-oab-exams", url="https://hf.co/x", fetched_at="t",
        kind="dataset", banca="FGV", carreira="oab",
    )
    db.upsert_source_document(bundle)
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    assert ingest_oab_exams(rows, source_id="hf-oab-exams", doc=bundle, db=db) == 2
    return settings, db


def test_pipeline_produces_a_usable_shortlist(tmp_path):
    settings, db = _pipeline(tmp_path)
    client = LLMClient(FakeBackend(_router))

    assert run_classify(db, client, load_taxonomy()) == 2
    assert run_vet(db, client, load_watchlist()) == 2

    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    # one of the two fixture rows is nullified -> rejected by the rule layer
    assert written["T1.2"] == 1

    md = (settings.shortlist_dir / "2026-2" / "T1.2.md").read_text(encoding="utf-8")
    assert "⚠" in md and "resposta_mudou_mas_util" in md
    assert "bqpp use" in md and "<details>" in md
    assert "FGV" in md
    db.close()


def test_nullified_question_never_reaches_a_shortlist(tmp_path):
    settings, db = _pipeline(tmp_path)
    client = LLMClient(FakeBackend(_router))
    run_classify(db, client, load_taxonomy())
    run_vet(db, client, load_watchlist())

    nullified = [q for q in db.iter_questions() if q.nullified]
    assert len(nullified) == 1
    assert nullified[0].vet_status == "rejected"

    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"])
    md = (settings.shortlist_dir / "2026-2" / "T1.2.md").read_text(encoding="utf-8")
    assert nullified[0].id not in md
    db.close()


def test_used_question_drops_out_of_the_next_semester(tmp_path):
    settings, db = _pipeline(tmp_path)
    client = LLMClient(FakeBackend(_router))
    run_classify(db, client, load_taxonomy())
    run_vet(db, client, load_watchlist())

    first = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T1.2"]
    )
    assert first["T1.2"] == 1
    picked = next(q for q in db.iter_questions() if q.vet_status == "flagged")
    record_use(db, picked.id, "2026.2", "T1.2")

    second = run_curate(
        db, load_taxonomy(), settings, semester="2027.1", subtopic_ids=["T1.2"]
    )
    assert second["T1.2"] == 0
    db.close()


def test_export_round_trips_the_whole_corpus(tmp_path):
    settings, db = _pipeline(tmp_path)
    client = LLMClient(FakeBackend(_router))
    run_classify(db, client, load_taxonomy())
    run_vet(db, client, load_watchlist())

    out = settings.export_dir / "questions.jsonl"
    assert export_jsonl(db, out) == 2
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {r["vet_status"] for r in records} == {"flagged", "rejected"}
    assert all(r["source"]["banca"] == "FGV" for r in records)
    db.close()


# ---- M2: the OAB padrão path, fixture PDF -> shortlist --------------------

def _m2_pipeline(tmp_path):
    """Same shape as _pipeline, but seeded from a real padrão PDF."""
    from bqpp.harvest.oab_site import Exam, IndexEntry, ingest_padrao

    settings = load_settings().model_copy(deep=True)
    settings.shortlist_dir = tmp_path / "shortlists"
    settings.export_dir = tmp_path / "export"

    db = Database.connect(tmp_path / "m2.sqlite")
    db.init_schema()
    n = ingest_padrao(
        (FIX / "oab_site" / "padrao_44.txt").read_text(encoding="utf-8"),
        source_id="oab-2f-penal",
        exam=Exam(id="17000", label="44º EXAME DE ORDEM UNIFICADO"),
        entry=IndexEntry(
            href="https://s.oab.org.br/arquivos/2025/11/b9ef8314.pdf",
            label="11/11/2025 - Padrão de respostas definitivo (Direito Penal)",
        ),
        rung="definitivo",
        banca="FGV",
        carreira="oab",
        db=db,
    )
    assert n == 5
    return settings, db


def _m2_router(call):
    if call["tier"] == "fast":
        return json.dumps({
            "discipline": "direito-processual-penal",
            "subtopic_ids": ["T2.4"],
            "difficulty": "hard",
            "note": "",
        })
    return json.dumps({
        "verdict": "ok",
        "reasons": [],
        "pedagogy_note": "Boa para discutir consunção e ANPP com a turma.",
    })


def test_a_padrao_reaches_a_shortlist_with_the_bancas_commentary(tmp_path):
    """The point of M2: banca-authored reasoning in the gabarito block."""
    settings, db = _m2_pipeline(tmp_path)
    client = LLMClient(FakeBackend(_m2_router))
    assert run_classify(db, client, load_taxonomy()) == 5
    assert run_vet(db, client, load_watchlist()) == 5

    written = run_curate(
        db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T2.4"]
    )
    assert written["T2.4"] == 5

    md = (settings.shortlist_dir / "2026-2" / "T2.4.md").read_text(encoding="utf-8")
    assert "Gabarito comentado da banca" in md
    assert "consunção" in md, "the banca's own reasoning, verbatim"
    assert "OAB 44º Exame (2ª fase)" in md and "2025" in md
    assert "https://s.oab.org.br" in md
    assert "dissertativa" in md and "peca" in md
    db.close()


def test_discursive_items_outrank_multiple_choice_in_a_shared_subtopic(tmp_path):
    """format_weights exist so the open formats the method needs surface first."""
    from bqpp.harvest.hf_datasets import ingest_oab_exams

    settings, db = _m2_pipeline(tmp_path)
    bundle = SourceDocument(
        id="bundle", source_id="hf-oab-exams", url="https://hf.co/x", fetched_at="t",
        kind="dataset", banca="FGV", carreira="oab",
    )
    db.upsert_source_document(bundle)
    rows = json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))
    ingest_oab_exams(rows, source_id="hf-oab-exams", doc=bundle, db=db)

    client = LLMClient(FakeBackend(_m2_router))
    run_classify(db, client, load_taxonomy())
    run_vet(db, client, load_watchlist())
    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T2.4"])

    md = (settings.shortlist_dir / "2026-2" / "T2.4.md").read_text(encoding="utf-8")
    # dissertativa 3.0 + ok 2.0 + rationale 1.5 + 2025 -> 7.75 beats mcq4 1.0 + ok 2.0 + 2010
    # -> 3.5, so the five slots go to the discursive items and no MCQ makes the cut.
    assert md.index("## 1. dissertativa") < md.index("## 5.")
    assert "mcq" not in md, "an MCQ should not displace a rationale-bearing discursive item"
    assert md.count("## ") >= 5
    db.close()


# ---- M3: Cebraspe certo/errado and a curated prova reach the shortlists ----

def _m3_router(call):
    if call["tier"] == "fast":
        return json.dumps({"discipline": "direito-processual-penal",
                           "subtopic_ids": ["T3.4"], "difficulty": "medium", "note": ""})
    return json.dumps({"verdict": "ok", "reasons": [],
                       "pedagogy_note": "Boa para discutir valoração da prova."})


def test_a_certo_errado_item_reaches_a_shortlist_with_its_comando(tmp_path):
    from bqpp.harvest.cebraspe import ingest_caderno

    settings = load_settings().model_copy(deep=True)
    settings.shortlist_dir = tmp_path / "shortlists"
    db = Database.connect(tmp_path / "m3.sqlite")
    db.init_schema()

    text = (FIX / "cebraspe" / "pc_df_26.txt").read_text(encoding="utf-8")
    assert ingest_caderno(
        text, source_id="cebraspe-cadernos",
        certame={"slug": "PC_DF_26_DELEGADO", "carreira": "delegado",
                 "certame": "PC/DF Delegado 2026", "exam_year": 2026},
        url="https://cdn.cebraspe.org.br/x.pdf", banca="CEBRASPE", db=db,
    ) > 0

    client = LLMClient(FakeBackend(_m3_router))
    run_classify(db, client, load_taxonomy())
    run_vet(db, client, load_watchlist())
    run_curate(db, load_taxonomy(), settings, semester="2026.2", subtopic_ids=["T3.4"])

    md = (settings.shortlist_dir / "2026-2" / "T3.4.md").read_text(encoding="utf-8")
    assert "Contexto (comando da banca)" in md
    assert "julgue" in md.lower(), "the comando must be rendered above the item"
    assert "CEBRASPE" in md and "PC/DF Delegado 2026" in md
    assert "certo_errado" in md
    db.close()


def test_the_corpus_now_spans_more_than_one_carreira(tmp_path):
    """The carreira tie-break in rank_candidates did nothing while every question
    was OAB; M3 is what activates it."""
    from bqpp.curate import rank_candidates
    from bqpp.models import Question, SourceDocument

    ranking = load_settings().ranking
    oab = SourceDocument(id="d1", source_id="s", url="u", fetched_at="t", kind="dataset",
                         carreira="oab", exam_year=2025)
    delegado = SourceDocument(id="d2", source_id="s", url="u", fetched_at="t",
                              kind="gabarito_justificado", carreira="delegado", exam_year=2025)
    a = Question(id="a", source_doc_id="d1", format="mcq4", stem="x", vet_status="ok")
    b = Question(id="b", source_doc_id="d2", format="mcq4", stem="y", vet_status="ok")

    ranked = rank_candidates([(a, oab), (b, delegado)], ranking, seen_carreiras={"oab"})
    assert ranked[0][1].carreira == "delegado", "an unseen carreira outranks a seen one"
