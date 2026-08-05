from bqpp.models import Question, SourceDocument, question_id, source_doc_id


def test_ids_are_deterministic_and_distinct():
    a = source_doc_id(b"hello")
    assert a == source_doc_id(b"hello") and len(a) == 64
    q1 = question_id(a, "12")
    assert q1 == question_id(a, "12")
    assert q1 != question_id(a, "13")


def test_question_choices_roundtrip():
    q = Question(
        id="x",
        source_doc_id="y",
        question_number="1",
        format="mcq4",
        stem="Enunciado",
        choices=[{"label": "A", "text": "alt A"}],
        answer_key="A",
    )
    assert q.choices[0]["label"] == "A"
    assert q.subtopic_ids == []
    assert q.vet_status == "unvetted"
    assert q.nullified is False


def test_source_document_requires_provenance():
    d = SourceDocument(
        id="abc",
        source_id="hf-oab-exams",
        url="https://hf.co/x",
        fetched_at="2026-08-05T10:00:00Z",
        kind="dataset",
        banca="FGV",
        carreira="oab",
        certame="OAB 2010-01",
        exam_year=2010,
    )
    assert d.banca == "FGV" and d.exam_year == 2010


def test_prompt_payload_excludes_pipeline_metadata():
    q = Question(
        id="x", source_doc_id="y", question_number="1", format="mcq4",
        stem="Enunciado", choices=[{"label": "A", "text": "a"}], answer_key="A",
        vet_status="ok", subtopic_ids=["T1.2"], classify_model="gemini",
    )
    payload = q.to_prompt_payload()
    assert set(payload) == {"format", "stem", "choices", "answer_key"}
