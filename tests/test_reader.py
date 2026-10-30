"""Проверки чтения и валидации workflow JSON.

Проверяется структура документа и сбор нескольких диагностик за один проход.
"""

import json
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from n8n_transcriper.reader import (
    WorkflowError,
    read_workflow,
    validate_workflow,
    validate_workflow_issues,
)


def test_reads_workflow_without_losing_data(tmp_path):
    model = {
        "id": "model-1", "name": "DeepSeek Chat Model",
        "type": "@n8n/n8n-nodes-langchain.lmChatDeepSeek",
        "parameters": {"model": "deepseek-flash"},
        "credentials": {"deepSeekApi": {"id": "placeholder-credential"}},
    }
    agent = {
        "id": "agent-1", "name": "AI Agent - Analyze CSV",
        "type": "@n8n/n8n-nodes-langchain.agent", "parameters": {},
    }
    data = {"nodes": [agent, model], "connections": {
        model["name"]: {"ai_languageModel": [[{
            "node": agent["name"], "type": "ai_languageModel", "index": 0,
        }]]},
    }}
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    workflow = read_workflow(path)
    assert len(workflow["nodes"]) == 2
    model = next(node for node in workflow["nodes"] if node["name"] == "DeepSeek Chat Model")
    assert model["parameters"]["model"] == "deepseek-flash"
    assert model["credentials"]["deepSeekApi"]["id"]
    assert workflow["connections"][model["name"]]["ai_languageModel"][0][0]["node"] == "AI Agent - Analyze CSV"


def test_utf8_bom_and_metadata():
    data = {"nodes": [], "connections": {}, "settings": {"binaryMode": "separate"}}
    with patch.object(Path, "open", return_value=io.StringIO(json.dumps(data))):
        assert read_workflow("workflow.json") == data


@pytest.mark.parametrize("data", [[], {}, {"nodes": [None], "connections": {}},
    {"nodes": [], "connections": {"missing": {}}}])
def test_invalid_structure(data):
    with pytest.raises(WorkflowError):
        validate_workflow(data)


def test_invalid_json():
    with patch.object(Path, "open", return_value=io.StringIO("{")):
        with pytest.raises(WorkflowError, match="строка 1, столбец 2"):
            read_workflow("bad.json")


def test_collects_multiple_structure_errors_in_one_pass():
    data = {
        "nodes": [
            None,
            {"id": "", "name": "", "type": "", "parameters": []},
            {"id": "", "name": "Existing", "type": "custom.node", "parameters": {}},
        ],
        "connections": {
            "missing-source": {
                "main": [[
                    {"node": "missing-target", "type": "", "index": -1},
                    None,
                ]],
            },
        },
    }

    issues = validate_workflow_issues(data)

    assert len(issues) >= 8
    assert any("Узел №1" in issue for issue in issues)
    assert any("не найден" in issue for issue in issues)
    with pytest.raises(WorkflowError) as error:
        validate_workflow(data)
    assert str(error.value).count("\n-") == len(issues)
