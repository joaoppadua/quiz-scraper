"""The manual seed path: professor-authored questions for subtopics no exam covers."""

import pytest
import yaml

from bqpp.config import load_taxonomy
from bqpp.db import Database
from bqpp.seed import SeedError, ingest_seeds, load_seeds

SEED = {
    "questions": [
        {
            "key": "t1-1-principios-cautelares",
            "subtopic_ids": ["T1.1"],
            "format": "dissertativa",
            "stem": "Disserte sobre a excepcionalidade das medidas cautelares pessoais. " * 3,
            "answer_rationale": "Espera-se referência ao Art. 282 do CPP e à subsidiariedade.",
            "banca": "autoral",
            "certame": "Aula de Processo Penal 2",
            "exam_year": 2026,
            "carreira": "outra",
        }
    ]
}


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def seed_file(tmp_path):
    p = tmp_path / "seed_questions.yaml"
    p.write_text(yaml.safe_dump(SEED, allow_unicode=True), encoding="utf-8")
    return p


def test_loads_entries_from_yaml(seed_file):
    assert [e.key for e in load_seeds(seed_file)] == ["t1-1-principios-cautelares"]


def test_an_absent_file_is_not_an_error(tmp_path):
    """The template ships empty; a professor who never seeds anything is fine."""
    assert load_seeds(tmp_path / "nope.yaml") == []


def test_an_empty_file_is_not_an_error(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("questions: []\n", encoding="utf-8")
    assert load_seeds(p) == []


def test_seeded_questions_are_pre_classified(seed_file, db):
    """The professor's own classification is not second-guessed by an LLM."""
    assert ingest_seeds(load_seeds(seed_file), db=db, taxonomy=load_taxonomy()) == 1
    q = next(db.iter_questions())
    assert q.subtopic_ids == ["T1.1"]
    assert q.discipline == "direito-processual-penal"
    assert q.classify_model == "manual"
    assert q.classified_at, "classify must skip this row"


def test_classify_skips_a_seeded_question(seed_file, db):
    ingest_seeds(load_seeds(seed_file), db=db, taxonomy=load_taxonomy())
    assert list(db.iter_questions(unclassified=True)) == []


def test_vetting_still_sees_it(seed_file, db):
    """Pre-classified, not pre-vetted: the law watchlist still applies."""
    ingest_seeds(load_seeds(seed_file), db=db, taxonomy=load_taxonomy())
    assert len(list(db.iter_questions(unvetted=True))) == 1


def test_provenance_is_recorded_per_entry(seed_file, db):
    ingest_seeds(load_seeds(seed_file), db=db, taxonomy=load_taxonomy())
    doc = db.get_source_document(next(db.iter_questions()).source_doc_id)
    assert doc.kind == "manual"
    assert doc.certame == "Aula de Processo Penal 2"
    assert doc.exam_year == 2026, "a seed still needs a year or the watchlist cannot fire"


def test_seeding_is_idempotent(seed_file, db):
    taxonomy = load_taxonomy()
    assert ingest_seeds(load_seeds(seed_file), db=db, taxonomy=taxonomy) == 1
    assert ingest_seeds(load_seeds(seed_file), db=db, taxonomy=taxonomy) == 0
    assert len(list(db.iter_questions())) == 1


def test_editing_a_seed_updates_in_place_under_force(tmp_path, db):
    taxonomy = load_taxonomy()
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(SEED, allow_unicode=True), encoding="utf-8")
    ingest_seeds(load_seeds(p), db=db, taxonomy=taxonomy)

    edited = {"questions": [{**SEED["questions"][0], "stem": "Texto revisado. " * 20}]}
    p.write_text(yaml.safe_dump(edited, allow_unicode=True), encoding="utf-8")
    ingest_seeds(load_seeds(p), db=db, taxonomy=taxonomy, force=True)

    questions = list(db.iter_questions())
    assert len(questions) == 1, "same key -> same row, not a second question"
    assert questions[0].stem.startswith("Texto revisado.")


def test_an_invalid_subtopic_id_is_rejected_loudly(tmp_path, db):
    p = tmp_path / "bad.yaml"
    p.write_text(
        yaml.safe_dump({"questions": [{**SEED["questions"][0], "subtopic_ids": ["T9.9"]}]},
                       allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(SeedError, match=r"T9\.9"):
        ingest_seeds(load_seeds(p), db=db, taxonomy=load_taxonomy())


def test_a_missing_stem_names_the_offending_entry(tmp_path, db):
    p = tmp_path / "bad.yaml"
    entry = {**SEED["questions"][0]}
    del entry["stem"]
    p.write_text(yaml.safe_dump({"questions": [entry]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SeedError, match="t1-1-principios-cautelares"):
        ingest_seeds(load_seeds(p), db=db, taxonomy=load_taxonomy())


def test_a_missing_subtopic_names_the_offending_entry(tmp_path, db):
    p = tmp_path / "bad.yaml"
    entry = {**SEED["questions"][0]}
    del entry["subtopic_ids"]
    p.write_text(yaml.safe_dump({"questions": [entry]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SeedError, match="subtopic_ids"):
        ingest_seeds(load_seeds(p), db=db, taxonomy=load_taxonomy())


def test_the_shipped_template_is_valid_and_empty():
    """It must parse, so a first `bqpp seed` never explodes on a fresh clone."""
    from bqpp.config import CONFIG_DIR

    assert load_seeds(CONFIG_DIR / "seed_questions.yaml") == []
