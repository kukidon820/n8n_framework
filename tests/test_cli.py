from typer.testing import CliRunner

from n8n_transcriper.cli import app


def test_help_is_in_russian():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "workflow n8n" in result.stdout


def test_missing_workflow_reports_error():
    result = CliRunner().invoke(app, ["inspect", "does-not-exist.json"])
    assert result.exit_code == 1
    assert "Ошибка" in result.output
