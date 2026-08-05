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
