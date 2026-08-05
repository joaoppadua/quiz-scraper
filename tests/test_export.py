import json

from bqpp.db import Database
from bqpp.export import export_jsonl
from bqpp.models import Question, SourceDocument


def test_export_joins_source_document(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d", source_id="hf-oab-exams", url="https://hf.co/x",
                       fetched_at="t", kind="dataset", banca="FGV", exam_year=2018)
    )
    db.upsert_question(
        Question(id="q1", source_doc_id="d", question_number="1", format="mcq4",
                 stem="s", choices=[{"label": "A", "text": "a"}], answer_key="A")
    )
    out = tmp_path / "questions.jsonl"
    assert export_jsonl(db, out) == 1
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["id"] == "q1" and rec["source"]["banca"] == "FGV"
    assert rec["source"]["url"] == "https://hf.co/x"
    db.close()


def test_export_is_one_object_per_line_and_utf8_clean(tmp_path):
    db = Database.connect(tmp_path / "t.sqlite")
    db.init_schema()
    db.upsert_source_document(
        SourceDocument(id="d", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    for i in range(3):
        db.upsert_question(
            Question(id=f"q{i}", source_doc_id="d", question_number=str(i), format="mcq4",
                     stem="Prisão em flagrante e audiência de custódia", answer_key="A")
        )
    out = tmp_path / "questions.jsonl"
    assert export_jsonl(db, out) == 3
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(json.loads(line) for line in lines)
    assert "audiência" in lines[0]  # not ê-escaped
    db.close()
