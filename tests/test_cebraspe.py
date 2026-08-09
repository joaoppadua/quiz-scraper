"""Cebraspe API client: discovery, URL construction and genre selection. No network."""

from pathlib import Path

import pytest

from bqpp.harvest.cebraspe import (
    cdn_url,
    parse_manifest,
    parse_seed,
    select_combined_caderno,
)

FIX = Path(__file__).parent / "fixtures" / "cebraspe"


def _manifest(slug):
    return parse_manifest((FIX / f"event_{slug.lower()}.json").read_bytes())


# ---- discovery (amendment C5) ---------------------------------------------

def test_seed_is_flattened_across_fase_groups():
    """The seed is 4 fase groups each carrying an `eventos` array, not a flat list.
    Reading it as a flat list yields 4 and silently loses the whole catalogue."""
    certames = parse_seed((FIX / "seed_page.json").read_bytes())
    assert len(certames) > 4, "a naive flat read returns 4 — this must fail that"
    slugs = {c.slug for c in certames}
    assert "PC_DF_26_DELEGADO" in slugs and "DP_DF_19_DEFENSOR" in slugs


def test_certames_carry_a_human_name():
    certames = parse_seed((FIX / "seed_page.json").read_bytes())
    pc = next(c for c in certames if c.slug == "PC_DF_26_DELEGADO")
    assert pc.name


def test_an_empty_body_is_not_found_not_a_crash():
    """An unknown slug returns HTTP 204 with an empty body (amendment C11)."""
    assert parse_manifest(b"") == []
    assert parse_manifest(b"   ") == []


# ---- URL construction (amendment C6) --------------------------------------

def test_cdn_url_ignores_the_extension_field():
    """tipoExtensaoArquivo is '_.pdf' while nomeArquivo already ends in '.pdf';
    joining them yields .pdf.pdf and a 404."""
    artifact = next(a for a in _manifest("PC_DF_26_DELEGADO") if a.name.lower().endswith(".pdf"))
    url = cdn_url("PC_DF_26_DELEGADO", artifact)
    assert url.count(".pdf") == 1
    assert not url.endswith(".pdf.pdf")


def test_cdn_url_has_the_verified_shape():
    artifact = next(a for a in _manifest("PC_DF_26_DELEGADO") if "JUSTIFICATIVAS" in a.description.upper())
    url = cdn_url("PC_DF_26_DELEGADO", artifact)
    assert url.startswith("https://cdn.cebraspe.org.br/concursos/PC_DF_26_DELEGADO/arquivos/")


def test_cdn_url_percent_encodes_and_preserves_case():
    from bqpp.harvest.cebraspe import Artifact

    a = Artifact(name="PROVA ÁREA 1.PDF", description="x")
    url = cdn_url("X_22", a)
    assert " " not in url
    assert "PROVA" in url and "prova" not in url.rsplit("/", 1)[-1].replace("PROVA", "")


# ---- genre selection (amendment C7) ---------------------------------------

def test_the_2026_caderno_is_found_by_its_description():
    a = select_combined_caderno(_manifest("PC_DF_26_DELEGADO"))
    assert a is not None
    assert "JUSTIFICATIVAS" in a.description.upper()


def test_the_2019_caderno_is_found_by_its_filename():
    """DP/DF 19's description is merely 'PROVA OBJETIVA'; only the _C_JUST.PDF
    filename betrays the genre. Description-only matching finds 1 of 2."""
    a = select_combined_caderno(_manifest("DP_DF_19_DEFENSOR"))
    assert a is not None
    assert a.name.upper().endswith("_C_JUST.PDF")


def test_a_certame_with_only_ordinary_justificativas_is_rejected():
    """TJ/PR 16's files match a loose 'prova' + 'justificativa' regex but contain
    no per-item rationale at all — the false positive that cost a wasted download."""
    assert select_combined_caderno(_manifest("TJ_PR_16_JUIZ")) is None


def test_no_files_yields_none():
    assert select_combined_caderno([]) is None


# ============================ ingestion ====================================

from bqpp.db import Database  # noqa: E402
from bqpp.harvest.cebraspe import ingest_caderno  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database.connect(tmp_path / "t.sqlite")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture(scope="module")
def caderno_text():
    return (FIX / "pc_df_26.txt").read_text(encoding="utf-8")


CERT = {"slug": "PC_DF_26_DELEGADO", "carreira": "delegado",
        "certame": "PC/DF Delegado 2026", "exam_year": 2026}


def _ingest(db, text, force=False, certame=None):
    return ingest_caderno(
        text, source_id="cebraspe-cadernos", certame=certame or CERT,
        url="https://cdn.cebraspe.org.br/concursos/PC_DF_26_DELEGADO/arquivos/x.pdf",
        banca="CEBRASPE", db=db, force=force,
    )


def test_ingest_writes_usable_items_only(db, caderno_text):
    from bqpp.parse.caderno import segment_caderno

    items = segment_caderno(caderno_text)
    usable = [i for i in items if i.usable]
    assert 0 < len(usable) < len(items), "the fixture must exercise the gate"
    assert _ingest(db, caderno_text) == len(usable)


def test_ingested_items_are_certo_errado_with_rationale(db, caderno_text):
    _ingest(db, caderno_text)
    for q in db.iter_questions():
        assert q.format == "certo_errado"
        assert q.answer_key in ("C", "E")
        assert q.answer_rationale
        assert q.stem_context, "the comando must travel with the item"


def test_one_source_document_carrying_provenance(db, caderno_text):
    _ingest(db, caderno_text)
    doc_ids = {q.source_doc_id for q in db.iter_questions()}
    assert len(doc_ids) == 1
    doc = db.get_source_document(doc_ids.pop())
    assert doc.banca == "CEBRASPE"
    assert doc.carreira == "delegado"
    assert doc.certame == "PC/DF Delegado 2026"
    assert doc.exam_year == 2026, "exam_year drives the law watchlist"
    assert doc.kind == "gabarito_justificado"


def test_ingest_is_idempotent(db, caderno_text):
    n = _ingest(db, caderno_text)
    assert n > 0
    assert _ingest(db, caderno_text) == 0
    assert len(list(db.iter_questions())) == n


def test_force_rewrites_without_destroying_llm_work(db, caderno_text):
    n = _ingest(db, caderno_text)
    q = next(db.iter_questions())
    db.update_classification(q.id, discipline="direito-processual-penal",
                             subtopic_ids=["T3.4"], difficulty="hard",
                             classified_note=None, classify_model="gemini",
                             classified_at="2026-08-09")
    assert _ingest(db, caderno_text, force=True) == n
    assert db.get_question(q.id).subtopic_ids == ["T3.4"]


def test_sibling_items_are_not_deduped_against_each_other(db, caderno_text):
    """Amendment C10 on real data: items sharing a comando must stay distinct."""
    _ingest(db, caderno_text)
    contexts = [q.stem_context for q in db.iter_questions()]
    assert len(contexts) > len(set(contexts)), "the fixture has a shared comando"


def test_a_document_without_enough_justificativas_is_rejected(db):
    """Content assertion (C7): a mis-selected file must fail loudly, not ingest empty."""
    from bqpp.harvest.cebraspe import CadernoRejected

    with pytest.raises(CadernoRejected):
        _ingest(db, "PROVA OBJETIVA\n1 Um item qualquer sem justificativa nenhuma.\n")
