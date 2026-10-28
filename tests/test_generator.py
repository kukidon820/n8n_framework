import subprocess
import sys
from pathlib import Path

import pytest

from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError, read_workflow


def test_generate_and_run_linear_project(tmp_path):
    workflow = {"nodes": [
        {"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
        {"id": "2", "name": "Build", "type": "n8n-nodes-base.code", "parameters": {
            "language": "pythonNative", "pythonCode": 'return [{"json": {"text": "готово"}}]'}},
        {"id": "3", "name": "File", "type": "n8n-nodes-base.convertToFile", "parameters": {
            "operation": "toText", "sourceProperty": "text", "options": {"fileName": "test.txt"}}},
    ], "connections": {
        "Start": {"main": [[{"node": "Build", "type": "main", "index": 0}]]},
        "Build": {"main": [[{"node": "File", "type": "main", "index": 0}]]},
    }}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", "import main; print(main.run()[0]['binary']['data']['data'].decode())"],
                            cwd=output, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "готово"
    assert (output / "conversion_report.md").exists()


def test_unsupported_example_fails_without_partial_output(tmp_path):
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    output = tmp_path / "generated"
    workflow = read_workflow(path)
    workflow["nodes"][-1]["parameters"]["unsupportedOption"] = True
    with pytest.raises(WorkflowError, match="HTTP-параметры"):
        generate_project(workflow, output)
    assert not output.exists()


def test_generated_set_reads_previous_node(tmp_path):
    workflow = {"nodes": [
        {"id": "1", "name": "Settings", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "parameters": {"assignments": {"assignments": [{"name": "mail", "type": "string", "value": "test@example.com"}]}}},
        {"id": "2", "name": "Copy", "type": "n8n-nodes-base.set", "typeVersion": 3.5,
         "parameters": {"assignments": {"assignments": [{"name": "to", "type": "string", "value": "={{ $('Settings').first().json.mail }}"}]}}},
    ], "connections": {"Settings": {"main": [[{"node": "Copy", "type": "main", "index": 0}]]}}}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-c", "import main; print(main.run()[0]['json']['to'])"],
                            cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "test@example.com"


def test_empty_output_stops_linear_chain(tmp_path):
    workflow = {"nodes": [
        {"id": "1", "name": "Empty", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "return []"}},
        {"id": "2", "name": "Must not run", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "raise RuntimeError('must not execute')"}},
    ], "connections": {"Empty": {"main": [[{"node": "Must not run", "type": "main", "index": 0}]]}}}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-c", "import main; assert main.run() == []"], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("setting,value", [("disabled", True), ("alwaysOutputData", True), ("executeOnce", True), ("onError", "continueRegularOutput"), ("retryOnFail", True)])
def test_unimplemented_execution_settings_fail_before_writing(tmp_path, setting, value):
    node = {"id": "1", "name": "Code", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "return _items"}, setting: value}
    output = tmp_path / "out"
    with pytest.raises(WorkflowError, match=setting):
        generate_project({"nodes": [node], "connections": {}}, output)
    assert not output.exists()


def test_fanout_branches_use_same_source(tmp_path):
    def code(name, source):
        return {"id": name, "name": name, "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": source}}
    workflow = {"nodes": [
        code("Source", 'return [{"json": {"value": 3}}]'),
        code("Double", '_items[0]["json"]["value"] *= 2\nreturn _items'),
        code("Triple", 'return [{"json": {"value": _items[0]["json"]["value"] * 3}}]'),
    ], "connections": {"Source": {"main": [[{"node": "Double", "type": "main", "index": 0},
                                                  {"node": "Triple", "type": "main", "index": 0}]]}}}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-c", "import main; assert main.run() == [{'json': {'value': 6}}, {'json': {'value': 9}}]"],
                            cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
