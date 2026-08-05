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
    for cmd in ("harvest", "classify", "vet", "curate", "use", "stats", "export"):
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
