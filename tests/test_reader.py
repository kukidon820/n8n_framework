import json
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from n8n_transcriper.reader import WorkflowError, read_workflow, validate_workflow


def test_reads_example_without_losing_data():
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    workflow = read_workflow(path)
    assert len(workflow["nodes"]) == 14
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
        with pytest.raises(WorkflowError, match="Cannot read workflow"):
            read_workflow("bad.json")
