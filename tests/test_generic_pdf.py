"""The curated-manifest adapter for official sources that cannot be enumerated."""

from pathlib import Path

import pytest
import yaml

from bqpp.db import Database
from bqpp.harvest.generic_pdf import ManifestError, ingest_prova, load_manifest

FIX = Path(__file__).parent / "fixtures" / "cebraspe"

ENTRY = {
    "id": "mprs-50",
    "url": "https://www.mprs.mp.br/x.pdf",
    "banca": "MPRS",
    "carreira": "mp",
    "certame": "MPRS 50º Concurso",
    "exam_year": 2025,
    "format": "mcq5",
    "columns": 1,
    "grid_style": "interleaved",
    "verified_on": "2026-08-09",
}


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture(scope="module")
def mprs_text():
    return (FIX / "mprs_50.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def mprs_grid():
    return (FIX / "mprs_50_grid.txt").read_text(encoding="utf-8")


# ---- manifest validation ---------------------------------------------------

def test_the_shipped_manifest_loads_and_validates():
    """A malformed manifest must fail at load, not halfway through a harvest."""
    from bqpp.config import CONFIG_DIR

    entries = load_manifest(CONFIG_DIR / "provas_manifest.yaml")
    assert len(entries) >= 2
    assert {e["id"] for e in entries} >= {"mprs-50", "mpf-31"}


def test_every_shipped_entry_records_when_it_was_verified():
    """These URLs cannot be reconstructed, so provenance includes a human check."""
    from bqpp.config import CONFIG_DIR

    for e in load_manifest(CONFIG_DIR / "provas_manifest.yaml"):
        assert e.get("verified_on"), f"{e['id']} has no verified_on date"


@pytest.mark.parametrize(
    "missing", ["id", "url", "banca", "carreira", "certame", "exam_year", "format",
                "columns", "grid_style", "verified_on"],
)
def test_a_missing_field_names_the_offending_entry(tmp_path, missing):
    entry = {k: v for k, v in ENTRY.items() if k != missing}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"provas": [entry]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManifestError, match="mprs-50" if missing != "id" else "entry"):
        load_manifest(p)


def test_an_absent_manifest_is_an_error(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nope.yaml")


# ---- ingestion -------------------------------------------------------------

def test_a_single_file_entry_ingests(db, mprs_text, mprs_grid):
    """MPRS carries prova and grid in one document."""
    n = ingest_prova(mprs_text + "\n" + mprs_grid, None, entry=ENTRY,
                     source_id="manual-provas", db=db)
    assert n > 0
    for q in db.iter_questions():
        assert q.format == "mcq5"
        assert len(q.choices) == 5
        # an annulled question has no key, and is kept flagged rather than dropped
        assert q.answer_key in list("ABCDE") or q.nullified


def test_a_two_file_entry_ingests(db, mprs_text, mprs_grid):
    """MPF publishes prova and gabarito separately."""
    n = ingest_prova(mprs_text, mprs_grid, entry=ENTRY, source_id="manual-provas", db=db)
    assert n > 0


def test_annulled_questions_are_flagged_not_dropped(db, mprs_text, mprs_grid):
    ingest_prova(mprs_text, mprs_grid, entry=ENTRY, source_id="manual-provas", db=db)
    nullified = [q for q in db.iter_questions() if q.nullified]
    assert nullified, "MPRS annuls 7 questions; they belong in the corpus, flagged"
    assert all(q.answer_key is None for q in nullified)


def test_provenance_reaches_the_source_document(db, mprs_text, mprs_grid):
    ingest_prova(mprs_text, mprs_grid, entry=ENTRY, source_id="manual-provas", db=db)
    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert doc.banca == "MPRS" and doc.carreira == "mp"
    assert doc.exam_year == 2025
    assert doc.certame == "MPRS 50º Concurso"


def test_ingest_is_idempotent(db, mprs_text, mprs_grid):
    n = ingest_prova(mprs_text, mprs_grid, entry=ENTRY, source_id="manual-provas", db=db)
    assert ingest_prova(mprs_text, mprs_grid, entry=ENTRY, source_id="manual-provas", db=db) == 0
    assert len(list(db.iter_questions())) == n


def test_a_question_with_no_answer_in_the_grid_is_skipped(db, mprs_text):
    """Better to drop a question than to open a class with the wrong key."""
    sparse = "1 A   2 B"
    n = ingest_prova(mprs_text, sparse, entry=ENTRY, source_id="manual-provas", db=db)
    assert n == 2
