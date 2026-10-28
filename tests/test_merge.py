import subprocess
import sys

import pytest

from n8n_transcriper.generator import generate_project
from n8n_transcriper.converters.merge import generate_merge_node
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


def make_combine(combine_by, options=None, **params):
    node = {"name": "Merge", "typeVersion": 3.2, "parameters": {"mode": "combine", "combineBy": combine_by,
            "options": options or {}, **params}}
    source, count = generate_merge_node(node, "run")
    namespace = {}
    exec(source, namespace)
    assert count == 2
    return namespace["run"]


def test_combine_by_position_deep_merge_and_unpaired():
    run = make_combine("combineByPosition", {"includeUnpaired": True})
    first = [{"json": {"id": 1, "nested": {"a": 1}, "list": [1, 2]}, "binary": {"file": {"data": b"x"}}},
             {"json": {"id": 2}}]
    second = [{"json": {"nested": {"b": 2}, "list": [9]}}]
    result = run([first, second])
    assert result[0]["json"] == {"id": 1, "nested": {"a": 1, "b": 2}, "list": [9, 2]}
    assert result[0]["binary"]["file"]["data"] == b"x"
    assert result[1]["json"] == {"id": 2}


def test_combine_all_conflicts_and_suffixed_fields():
    first = [{"json": {"value": "a"}}, {"json": {"value": "b"}}]
    second = [{"json": {"value": "c"}}, {"json": {"value": "d"}}]
    run = make_combine("combineAll", {"clashHandling": {"values": {"resolveClash": "addSuffix"}}})
    result = run([first, second])
    assert len(result) == 4
    assert result[0]["json"] == {"value_1": "a", "value_2": "c"}


def test_combine_by_fields_matches_types_and_duplicates():
    run = make_combine("combineByFields", fieldsToMatchString="id")
    result = run([[{"json": {"id": 1, "left": 1}}, {"json": {"id": "1"}}],
                  [{"json": {"id": 1, "right": "a"}}, {"json": {"id": 1, "right": "b"}}]])
    assert [entry["json"]["right"] for entry in result] == ["a", "b"]
    result = run([[{"json": {"id": 1.0}}, {"json": {"id": True}}], [{"json": {"id": 1}}]])
    assert len(result) == 1


def test_unsupported_merge_modes_fail():
    with pytest.raises(WorkflowError, match="Append и Combine"):
        generate_merge_node({"name": "Merge", "typeVersion": 3, "parameters": {"mode": "chooseBranch"}}, "run")


def test_generated_combine_workflow(tmp_path):
    workflow = {"nodes": [node("Start", "return _items"),
        node("Left", 'return [{"json": {"left": 1, "same": "left"}}]'),
        node("Right", 'return [{"json": {"right": 2, "same": "right"}}]'),
        {"id": "m", "name": "Merge", "type": "n8n-nodes-base.merge", "typeVersion": 3.2,
         "parameters": {"mode": "combine", "combineBy": "combineByPosition"}}],
        "connections": {"Start": {"main": [[edge("Left"), edge("Right")]]},
                        "Left": {"main": [[edge("Merge", 0)]]}, "Right": {"main": [[edge("Merge", 1)]]}}}
    output = tmp_path / "output"
    generate_project(workflow, output)
    script = 'import main; assert main.run()[0]["json"] == {"left": 1, "right": 2, "same": "right"}'
    result = subprocess.run([sys.executable, "-c", script], cwd=output, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode,expected", [
    ("keepMatches", [(1, "both")]),
    ("keepNonMatches", [(2, "left"), (3, "right")]),
    ("keepEverything", [(1, "both"), (2, "left"), (3, "right")]),
    ("enrichInput1", [(1, "both"), (2, "left")]),
    ("enrichInput2", [(1, "both"), (3, "right")]),
])
def test_combine_join_modes(mode, expected):
    run = make_combine("combineByFields", fieldsToMatchString="id", joinMode=mode)
    first = [{"json": {"id": 1, "left": True}}, {"json": {"id": 2, "left": True}}]
    second = [{"json": {"id": 1, "right": True}}, {"json": {"id": 3, "right": True}}]
    result = run([first, second])
    actual = [(entry["json"]["id"], "both" if "left" in entry["json"] and "right" in entry["json"] else "left" if "left" in entry["json"] else "right") for entry in result]
    assert actual == expected
    if mode == "keepNonMatches":
        assert [entry["json"]["_source"] for entry in result] == ["input1", "input2"]


def test_combine_by_nested_or_literal_dot_fields():
    nested = make_combine("combineByFields", fieldsToMatchString="person.id")
    assert len(nested([[{"json": {"person": {"id": 4}}}], [{"json": {"person": {"id": 4}}}]])) == 1
    literal = make_combine("combineByFields", {"disableDotNotation": True}, fieldsToMatchString="person.id")
    assert len(literal([[{"json": {"person.id": 4}}], [{"json": {"person.id": 4}}]])) == 1
    assert literal([[{"json": {"person": {"id": 4}}}], [{"json": {"person": {"id": 4}}}]]) == []


@pytest.mark.parametrize("left,right", [(3, "3"), (True, "TRUE"), (False, 0), (None, ""), ([1], "[1]")])
def test_combine_fuzzy_comparison(left, right):
    run = make_combine("combineByFields", {"fuzzyCompare": True}, fieldsToMatchString="id")
    assert len(run([[{"json": {"id": left}}], [{"json": {"id": right}}]])) == 1
