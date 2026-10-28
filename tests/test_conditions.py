import json
import subprocess
import sys

import pytest

from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def condition(value, operation="equals", kind="string"):
    return {"options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
            "conditions": [{"leftValue": "={{ $json.status }}", "rightValue": value,
                            "operator": {"type": kind, "operation": operation}}]}


def code_node(name, value):
    return {"id": name, "name": name, "type": "n8n-nodes-base.code", "parameters": {
        "language": "pythonNative", "pythonCode": f'return [{{"json": {{"branch": {value!r}}}}} for item in _items]'}}


def edge(name):
    return {"node": name, "type": "main", "index": 0}


def execute(output, items):
    script = f"import json, main; print(json.dumps(main.run({items!r})))"
    result = subprocess.run([sys.executable, "-c", script], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_if_routes_items_to_both_branches(tmp_path):
    node = {"id": "if", "name": "IF", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
            "parameters": {"conditions": condition("yes"), "options": {}}}
    workflow = {"nodes": [node, code_node("True", "true"), code_node("False", "false")],
                "connections": {"IF": {"main": [[edge("True")], [edge("False")]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    result = execute(output, [{"json": {"status": "yes"}}, {"json": {"status": "no"}}])
    assert sorted(item["json"]["branch"] for item in result) == ["false", "true"]
    assert execute(output, [{"json": {"status": "no"}}]) == [{"json": {"branch": "false"}}]


@pytest.mark.parametrize("all_matches,expected", [(False, ["first"]), (True, ["first", "second"])])
def test_switch_matching_and_fallback(tmp_path, all_matches, expected):
    node = {"id": "s", "name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3.2,
            "parameters": {"rules": {"values": [{"conditions": condition("yes")}, {"conditions": condition("yes")}]},
                           "options": {"allMatchingOutputs": all_matches, "fallbackOutput": "extra"}}}
    workflow = {"nodes": [node, code_node("First", "first"), code_node("Second", "second"), code_node("Fallback", "fallback")],
                "connections": {"Switch": {"main": [[edge("First")], [edge("Second")], [edge("Fallback")]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    result = execute(output, [{"json": {"status": "yes"}}])
    assert sorted(item["json"]["branch"] for item in result) == expected
    assert execute(output, [{"json": {"status": "other"}}]) == [{"json": {"branch": "fallback"}}]


def test_merge_rejected(tmp_path):
    node = {"id": "if", "name": "IF", "type": "n8n-nodes-base.if", "parameters": {"conditions": condition("x")}}
    workflow = {"nodes": [node, code_node("Join", "join")], "connections": {"IF": {"main": [[edge("Join")], [edge("Join")]]}}}
    with pytest.raises(WorkflowError, match="Merge"):
        generate_project(workflow, tmp_path / "output")


def test_unselected_branch_is_never_executed(tmp_path):
    node = {"id": "if", "name": "IF", "type": "n8n-nodes-base.if", "parameters": {"conditions": condition(True, "true", "boolean")}}
    fails = code_node("Bad", "bad")
    fails["parameters"]["pythonCode"] = 'raise RuntimeError("ветка не должна выполняться")'
    workflow = {"nodes": [node, code_node("Good", "good"), fails],
                "connections": {"IF": {"main": [[edge("Good")], [edge("Bad")]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    assert execute(output, [{"json": {"status": True}}]) == [{"json": {"branch": "good"}}]


def test_switch_fallback_routes_to_existing_output(tmp_path):
    node = {"id": "s", "name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3.2,
            "parameters": {"rules": {"values": [{"conditions": condition("yes")}, {"conditions": condition("maybe")}]},
                           "options": {"fallbackOutput": 1}}}
    workflow = {"nodes": [node, code_node("First", "first"), code_node("Second", "second")],
                "connections": {"Switch": {"main": [[edge("First")], [edge("Second")]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    assert execute(output, [{"json": {"status": "other"}}]) == [{"json": {"branch": "second"}}]
