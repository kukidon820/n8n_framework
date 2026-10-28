import subprocess
import sys

import pytest

from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def node(name, code):
    return {"id": name, "name": name, "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": code}}


def edge(name, input_index=0):
    return {"node": name, "type": "main", "index": input_index}


def test_merge_append_preserves_input_order(tmp_path):
    workflow = {"nodes": [node("Start", "return _items"),
        node("First", 'return [{"json": {"value": "first"}}]'),
        node("Second", 'return [{"json": {"value": "second"}}]'),
        {"id": "m", "name": "Merge", "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "parameters": {"numberInputs": 2}},
        node("After", 'return [{"json": {"values": [i["json"]["value"] for i in _items]}}]')],
        "connections": {"Start": {"main": [[edge("First"), edge("Second")]]},
                        "First": {"main": [[edge("Merge", 1)]]}, "Second": {"main": [[edge("Merge", 0)]]},
                        "Merge": {"main": [[edge("After")]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    script = 'import main; assert main.run() == [{"json": {"values": ["second", "first"]}}]'
    result = subprocess.run([sys.executable, "-c", script], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_merge_without_all_inputs_fails(tmp_path):
    workflow = {"nodes": [node("Start", "return _items"),
        {"id": "m", "name": "Merge", "type": "n8n-nodes-base.merge", "typeVersion": 3, "parameters": {}}],
        "connections": {"Start": {"main": [[edge("Merge")]]}}}
    with pytest.raises(WorkflowError, match="каждый вход"):
        generate_project(workflow, tmp_path / "output")
