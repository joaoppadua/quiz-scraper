from typer.testing import CliRunner

from bqpp.cli import app

runner = CliRunner()


def test_stats_runs_on_empty_db(tmp_path):
    """M0 definition of done."""
    result = runner.invoke(app, ["stats", "--db", str(tmp_path / "empty.sqlite")])
    assert result.exit_code == 0, result.output
    assert "Total questions: 0" in result.stdout


def test_stats_lists_every_subtopic_including_empty_ones(tmp_path):
    result = runner.invoke(app, ["stats", "--db", str(tmp_path / "empty.sqlite")])
    assert "T1.1" in result.stdout and "T4.3" in result.stdout


def test_all_spec_commands_are_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("harvest", "classify", "vet", "curate", "use", "stats", "export", "history"):
        assert cmd in result.stdout


def test_dry_run_flag_exists_on_pipeline_commands():
    for cmd in ("harvest", "classify", "vet", "curate"):
        assert "--dry-run" in runner.invoke(app, [cmd, "--help"]).stdout


def test_missing_key_exits_cleanly_without_a_traceback(tmp_path, monkeypatch):
    import bqpp.llm.factory as factory

    # build_client() calls load_dotenv(), which would pick up a real .env on the
    # developer's machine and make this test pass or fail by accident.
    monkeypatch.setattr(factory, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = runner.invoke(app, ["classify", "--db", str(tmp_path / "e.sqlite")])
    assert result.exit_code == 1
    assert "GEMINI_API_KEY is not set" in result.output
    assert "Traceback" not in result.output


def test_history_is_empty_on_a_fresh_db(tmp_path):
    result = runner.invoke(app, ["history", "--db", str(tmp_path / "e.sqlite")])
    assert result.exit_code == 0
    assert "nada registrado" in result.output


def test_history_surfaces_the_note(tmp_path):
    """--note is otherwise write-only: nothing else in the CLI reads it back."""
    from bqpp.curate import record_use
    from bqpp.db import Database
    from bqpp.models import Question, SourceDocument

    path = tmp_path / "h.sqlite"
    d = Database.connect(path)
    d.init_schema()
    d.upsert_source_document(
        SourceDocument(id="d", source_id="s", url="u", fetched_at="t", kind="dataset")
    )
    d.upsert_question(
        Question(id="q1", source_doc_id="d", question_number="1", format="mcq4",
                 stem="Enunciado de teste", choices=[{"label": "A", "text": "a"}],
                 answer_key="A", subtopic_ids=["T1.2"])
    )
    record_use(d, "q1", "2026.2", "T1.2", "excelente")
    d.close()

    result = runner.invoke(app, ["history", "--db", str(path)])
    assert result.exit_code == 0
    assert "2026.2" in result.output
    assert "T1.2" in result.output
    assert "excelente" in result.output


# ---- M2: adapter dispatch and the parse command ---------------------------

def _isolate_cache(monkeypatch, tmp_path):
    """Point the fetch cache at a cold tmp dir for this test."""
    from bqpp.config import load_settings

    settings = load_settings().model_copy(deep=True)
    settings.raw_dir = tmp_path / "raw"
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "export"
    settings.shortlist_dir = tmp_path / "shortlists"
    monkeypatch.setattr("bqpp.cli.load_settings", lambda *a, **k: settings)

def test_parse_is_registered_with_the_other_spec_commands():
    """Spec §12 lists `parse`; it had no implementation until M2."""
    assert "parse" in runner.invoke(app, ["--help"]).stdout
    assert runner.invoke(app, ["parse", "--help"]).exit_code == 0


def test_parse_supports_dry_run_and_verbose():
    out = runner.invoke(app, ["parse", "--help"]).stdout
    assert "--dry-run" in out and "--source" in out


def test_harvest_dry_run_opens_no_socket_and_writes_nothing(tmp_path, monkeypatch):
    """Uses the REAL source id. The earlier version passed --source no-such-source,
    which matched nothing in sources.yaml, so the exploding opener was never reached
    and the test asserted nothing at all."""
    import bqpp.harvest.http as http_mod

    def explode(*a, **k):  # pragma: no cover - the point is that it never runs
        raise AssertionError("--dry-run must not reach the network")

    monkeypatch.setattr(http_mod, "_urllib_opener", explode)
    _isolate_cache(monkeypatch, tmp_path)  # a warm real cache must not mask a regression
    db_path = tmp_path / "dry.sqlite"
    result = runner.invoke(
        app, ["harvest", "--source", "oab-2f-penal", "--dry-run", "--db", str(db_path)]
    )
    assert result.exit_code == 0, result.output

    from bqpp.db import Database

    db = Database.connect(db_path)
    db.init_schema()
    assert list(db.iter_questions()) == []
    db.close()


def test_parse_does_not_run_adapters_that_have_no_offline_mode(tmp_path, monkeypatch):
    """`bqpp parse` used to call the HuggingFace adapter online; with --force that
    re-ingested every HF row from scratch and wiped its classification and vetting."""
    import bqpp.harvest.hf_datasets as hf

    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("parse must not reach HuggingFace")

    monkeypatch.setattr(hf, "_download_parquet", explode)
    monkeypatch.setattr("bqpp.harvest.http._urllib_opener", explode)
    _isolate_cache(monkeypatch, tmp_path)
    result = runner.invoke(app, ["parse", "--db", str(tmp_path / "p.sqlite")])
    assert result.exit_code == 0, result.output
    assert "no offline mode" in result.stdout


def test_an_unknown_adapter_is_reported_not_crashed(tmp_path, monkeypatch):
    from bqpp.harvest.registry import SourceEntry

    monkeypatch.setattr(
        "bqpp.harvest.registry.load_sources",
        lambda *a, **k: [SourceEntry(id="future", adapter="cebraspe", params={})],
    )
    result = runner.invoke(app, ["harvest", "--db", str(tmp_path / "t.sqlite")])
    assert result.exit_code == 0, result.output
    assert "future" in result.stdout
