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
    assert (output / "pyproject.toml").is_file()
    assert (output / "uv.lock").is_file()
    assert (output / "requirements.txt").is_file()
    assert "uv sync --locked" in (output / "README.md").read_text(encoding="utf-8")


def test_validate_checks_conversion_without_creating_output(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({
        "name": "CLI validation",
        "nodes": [{
            "id": "1", "name": "Build", "type": "n8n-nodes-base.code",
            "parameters": {"language": "pythonNative", "pythonCode": "return _items"},
        }],
        "connections": {},
    }), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 0, result.output
    assert "Проверка workflow пройдена" in result.output
    assert "Узлов: 1" in result.output
    assert "Синтаксис сгенерированного Python проверен" in result.output
    assert not (tmp_path / "generated").exists()


def test_validate_reports_node_and_stage_for_unsupported_node(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({
        "nodes": [{
            "id": "1", "name": "Unsupported node", "type": "custom.unknown",
            "parameters": {},
        }],
        "connections": {},
    }), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 1
    assert "Проверка workflow не пройдена" in result.output
    assert "Обнаружено проблем: 1" in result.output
    assert "[проверка узлов и настроек]" in result.output
    assert "Unsupported node" in result.output


def test_validate_reports_all_unsupported_nodes_and_continues(tmp_path):
    workflow = tmp_path / "workflow.json"
    nodes = [
        {"id": "trigger", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
        {"id": "mail-1", "name": "Mail provider 1", "type": "custom.mailProvider", "parameters": {}},
        {"id": "mail-2", "name": "Mail provider 2", "type": "custom.mailProvider", "parameters": {}},
    ]
    workflow.write_text(json.dumps({
        "nodes": nodes,
        "connections": {
            "Start": {"main": [[{"node": "Mail provider 1", "type": "main", "index": 0}]]},
            "Mail provider 1": {"main": [[{"node": "Mail provider 2", "type": "main", "index": 0}]]},
        },
    }), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 1
    assert "Обнаружено проблем: 2" in result.output
    assert "Mail provider 1: неподдерживаемый тип custom.mailProvider" in result.output
    assert "Mail provider 2: неподдерживаемый тип custom.mailProvider" in result.output


def test_validate_supports_two_email_send_nodes_with_shared_smtp_credentials(tmp_path):
    workflow = tmp_path / "workflow.json"
    credential = {"smtp": {"id": "smtp-account", "name": "SMTP account"}}
    nodes = [
        {"id": "trigger", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
        {
            "id": "email-1", "name": "Send an Email", "type": "n8n-nodes-base.emailSend",
            "typeVersion": 2.1,
            "parameters": {
                "fromEmail": "sender@example.com", "toEmail": "first@example.com",
                "subject": "First", "emailFormat": "text", "text": "First message", "options": {},
            },
            "credentials": credential,
        },
        {
            "id": "email-2", "name": "Send an Email1", "type": "n8n-nodes-base.emailSend",
            "typeVersion": 2.1,
            "parameters": {
                "fromEmail": "sender@example.com", "toEmail": "second@example.com",
                "subject": "Second", "emailFormat": "text", "text": "Second message", "options": {},
            },
            "credentials": credential,
        },
    ]
    workflow.write_text(json.dumps({
        "nodes": nodes,
        "connections": {
            "Start": {"main": [[{"node": "Send an Email", "type": "main", "index": 0}]]},
            "Send an Email": {"main": [[{"node": "Send an Email1", "type": "main", "index": 0}]]},
        },
    }), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 0, result.output
    assert "Проверка workflow пройдена" in result.output
    assert "Синтаксис сгенерированного Python проверен" in result.output


def test_validate_reports_structure_and_node_errors_together(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({
        "nodes": [None, {
            "id": "unknown", "name": "Unsupported node",
            "type": "custom.unknown", "parameters": {},
        }],
        "connections": {
            "unknown-source": {"main": [[{"node": "missing-target", "type": "main", "index": 0}]]},
        },
    }), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 1
    assert "Узел №1 должен быть объектом" in result.output
    assert "Unsupported node: неподдерживаемый тип custom.unknown" in result.output
    assert "Не найден исходный узел соединения" in result.output
    assert "целевой узел «missing-target» не найден" in result.output


def test_validate_reports_json_parse_location(tmp_path):
    workflow = tmp_path / "broken.json"
    workflow.write_text('{"nodes": [}', encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(workflow)])

    assert result.exit_code == 1
    assert "Файл:" in result.output
    assert "строка 1, столбец" in result.output
