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


@pytest.mark.parametrize("setting,value", [("disabled", True), ("alwaysOutputData", "true"), ("executeOnce", 1), ("onError", "continueRegularOutput"), ("retryOnFail", True)])
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


def test_module_names_stable_when_node_list_reordered(tmp_path):
    first = {"id": "stable-1", "name": "My node", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "return _items"}}
    second = {"id": "stable-2", "name": "My-node", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "return _items"}}
    connections = {"My node": {"main": [[{"node": "My-node", "type": "main", "index": 0}]]}}
    first_output, second_output = tmp_path / "first", tmp_path / "second"
    generate_project({"nodes": [first, second], "connections": connections}, first_output)
    generate_project({"nodes": [second, first], "connections": connections}, second_output)
    names = {p.name for p in (first_output / "python_nodes").glob("*.py")}
    assert names == {p.name for p in (second_output / "python_nodes").glob("*.py")}
    assert len(names) == 3
    assert all(name == "__init__.py" or name.startswith("my_node_") for name in names)


@pytest.mark.parametrize("kind", ["n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.webhook", "n8n-nodes-base.manualTrigger"])
def test_triggers_are_skipped_and_input_is_manual(tmp_path, kind):
    workflow = {"nodes": [
        {"id": "1", "name": "Start", "type": kind, "parameters": {}},
        {"id": "2", "name": "Process", "type": "n8n-nodes-base.code", "parameters": {"language": "pythonNative", "pythonCode": "return _items"}},
    ], "connections": {"Start": {"main": [[{"node": "Process", "type": "main", "index": 0}]]}}}
    output = tmp_path / "out"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-c", "import main; assert main.run([{'json': {'x': 1}}]) == [{'json': {'x': 1}}]"], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_execute_once_and_always_output_data(tmp_path):
    first = {"id": "1", "name": "First", "type": "n8n-nodes-base.code", "executeOnce": True,
             "parameters": {"language": "pythonNative", "pythonCode": 'assert len(_items) == 1\nreturn []'}, "alwaysOutputData": True}
    second = {"id": "2", "name": "Second", "type": "n8n-nodes-base.code", "parameters": {
        "language": "pythonNative", "pythonCode": 'return [{"json": {"received": _items[0]["json"]}}]'}}
    workflow = {"nodes": [first, second], "connections": {"First": {"main": [[{"node": "Second", "type": "main", "index": 0}]]}}}
    output = tmp_path / "out"
    generate_project(workflow, output)
    script = 'import main; assert main.run([{"json": {"n": 1}}, {"json": {"n": 2}}]) == [{"json": {"received": {}}}]'
    result = subprocess.run([sys.executable, "-c", script], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
