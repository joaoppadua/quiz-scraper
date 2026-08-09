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
    # stem_context is content, not pipeline metadata: a certo/errado item cannot be
    # classified or vetted without the comando it hangs off.
    assert set(payload) == {"format", "stem", "stem_context", "choices", "answer_key"}


# ---- dedup key: boilerplate stems ------------------------------------------

def test_questions_with_boilerplate_stems_are_distinguished_by_their_choices():
    """MPF writes 'Assinale a opção correta:' as the stem of five different
    questions and puts the content in the alternatives. Hashing the stem alone
    collapses them to one and silently drops four."""
    from bqpp.models import content_hash

    stem = "Assinale a opção correta:"
    a = content_hash(stem, [{"label": "A", "text": "Medida provisória pode criar tipo penal."},
                            {"label": "B", "text": "Lei ordinária basta."}])
    b = content_hash(stem, [{"label": "A", "text": "São excludentes de culpabilidade."},
                            {"label": "B", "text": "A inimputabilidade não exclui."}])
    assert a != b


def test_the_same_question_still_hashes_the_same():
    from bqpp.models import content_hash

    choices = [{"label": "A", "text": "Uma alternativa."}]
    assert content_hash("Pergunta?", choices) == content_hash("  PERGUNTA?  ", choices)


def test_choiceless_questions_hash_exactly_as_before():
    """M2's oab-bench/padrão dedup depends on the stem-prefix behaviour."""
    from bqpp.models import content_hash, stem_hash

    stem = "Enunciado discursivo qualquer. " * 20
    assert content_hash(stem) == stem_hash(stem)
    assert content_hash(stem, None) == stem_hash(stem)
