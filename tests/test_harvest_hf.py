"""Harvest tests run against committed real-row fixtures — no network."""

import json
from pathlib import Path

import pytest

from bqpp.db import Database
from bqpp.harvest.hf_datasets import ingest_oab_bench, ingest_oab_exams
from bqpp.harvest.registry import load_sources
from bqpp.models import SourceDocument

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


def _doc(sid="hf-oab-exams"):
    return SourceDocument(
        id="doc-" + sid, source_id=sid, url="https://huggingface.co/x",
        fetched_at="2026-08-05T00:00:00Z", kind="dataset", banca="FGV", carreira="oab",
    )


def _exam_rows():
    return json.loads((FIX / "oab_exams_sample.json").read_text(encoding="utf-8"))


def _bench_payload():
    return json.loads((FIX / "oab_bench_sample.json").read_text(encoding="utf-8"))


def test_oab_exams_maps_columns_and_filters_by_question_type(db):
    doc = _doc()
    db.upsert_source_document(doc)
    n = ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db)
    assert n == 2  # 3 rows in; the ETHICS one is dropped
    q = next(db.iter_questions())
    assert q.format in ("mcq4", "mcq5")
    assert len(q.choices) == 4 and q.choices[0]["label"] == "A"
    assert q.answer_key in "ABCDE"
    assert q.stem and q.answer_rationale is None


def test_oab_exams_marks_nullified(db):
    doc = _doc()
    db.upsert_source_document(doc)
    ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db)
    assert any(q.nullified for q in db.iter_questions())


def test_oab_exams_ingest_is_idempotent(db):
    doc = _doc()
    db.upsert_source_document(doc)
    assert ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db) == 2
    assert ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db) == 0


def test_oab_exams_question_numbers_are_unique_across_exams(db):
    doc = _doc()
    db.upsert_source_document(doc)
    ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db)
    numbers = [q.question_number for q in db.iter_questions()]
    assert len(numbers) == len(set(numbers))
    assert all("-" in n for n in numbers)  # "{exam_id}-{n}", not a bare index


def test_oab_exams_registers_per_exam_provenance(db):
    """A dataset bundle spans many certames; each question must carry its own
    exam_year, or the vetting watchlist rule can never fire."""
    doc = _doc()
    db.upsert_source_document(doc)
    ingest_oab_exams(_exam_rows(), source_id="hf-oab-exams", doc=doc, db=db)
    for q in db.iter_questions():
        src = db.get_source_document(q.source_doc_id)
        assert src is not None
        assert src.exam_year is not None and 2009 < src.exam_year < 2030
        assert src.certame and src.certame.startswith("OAB ")
        assert src.banca == "FGV" and src.carreira == "oab"
        assert src.id != doc.id  # a child document, not the bundle


def test_oab_bench_registers_per_exam_provenance(db):
    payload = _bench_payload()
    doc = _doc("hf-oab-bench")
    db.upsert_source_document(doc)
    ingest_oab_bench(
        payload["questions"], payload["guidelines"],
        source_id="hf-oab-bench", doc=doc, db=db,
    )
    for q in db.iter_questions():
        src = db.get_source_document(q.source_doc_id)
        assert src.certame == "OAB 39º Exame (2ª fase)"
        assert src.exam_year is None  # no year column in this dataset


def test_oab_bench_joins_guidelines_and_tags_peca(db):
    payload = _bench_payload()
    doc = _doc("hf-oab-bench")
    db.upsert_source_document(doc)
    n = ingest_oab_bench(
        payload["questions"], payload["guidelines"],
        source_id="hf-oab-bench", doc=doc, db=db,
    )
    assert n == 2  # 1 penal questao + 1 penal peca; the civil row is dropped
    by_fmt = {q.format: q for q in db.iter_questions()}
    assert set(by_fmt) == {"dissertativa", "peca"}
    assert by_fmt["dissertativa"].answer_rationale  # official guideline joined in
    assert by_fmt["dissertativa"].answer_key is None
    assert by_fmt["peca"].answer_key is None


def test_oab_bench_stem_includes_numbered_subitems(db):
    payload = _bench_payload()
    doc = _doc("hf-oab-bench")
    db.upsert_source_document(doc)
    ingest_oab_bench(
        payload["questions"], payload["guidelines"],
        source_id="hf-oab-bench", doc=doc, db=db,
    )
    diss = next(q for q in db.iter_questions() if q.format == "dissertativa")
    src = next(
        q for q in payload["questions"] if q["question_id"].endswith("questao_1")
    )
    for i, turn in enumerate([t for t in src["turns"] if t.strip()], start=1):
        assert f"**{chr(64 + i)})**" in diss.stem
        assert turn.strip()[:40] in diss.stem


def test_sources_registry_carries_the_corrected_filters():
    entries = {e.id: e for e in load_sources()}
    assert entries["hf-oab-exams"].params["question_type_filter"] == [
        "CRIMINAL-PROCEDURE", "CRIMINAL",
    ]
    assert entries["hf-oab-bench"].params["category_suffix_filter"] == ["direito_penal"]
