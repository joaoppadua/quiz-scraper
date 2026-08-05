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
