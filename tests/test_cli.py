import json
import subprocess
import sys
from pathlib import Path

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


def test_convert_py_matches_documented_command(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"nodes": [{
        "id": "1", "name": "Build", "type": "n8n-nodes-base.code",
        "parameters": {"language": "pythonNative", "pythonCode": "return _items"},
    }], "connections": {}}), encoding="utf-8")
    output = tmp_path / "generated"
    script = Path(__file__).resolve().parent.parent / "convert.py"
    result = subprocess.run(
        [sys.executable, str(script), str(workflow), "--out", str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "main.py").is_file()
