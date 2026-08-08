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
