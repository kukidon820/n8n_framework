import pytest
import json
from pathlib import Path

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError
from n8n_transcriper.reader import read_workflow


def test_named_node_and_nested_paths():
    result = compile_expression("={{ $('Settings').first().json.mail_to }}", node="Edit", parameter="value")
    assert result.code == "results['Settings'][0]['json']['mail_to']"
    assert result.dependencies == {"Settings"}


def test_template_and_array():
    result = compile_expression("ID: {{ $json.data[0].id }}", node="test", parameter="text")
    assert result.code == "'ID: ' + str(item['json']['data'][0]['id']) + ''"


def test_unsupported_javascript_does_not_silently_convert():
    with pytest.raises(WorkflowError, match="узел|Узел"):
        compile_expression("={{ $json.id + 1 }}", node="test", parameter="value")


def test_real_prompt_and_gmail_body():
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    nodes = {n["name"]: n for n in read_workflow(path)["nodes"]}
    prompt = compile_expression(nodes["AI Agent - Analyze CSV"]["parameters"]["text"], node="AI", parameter="text")
    assert prompt.dependencies == {"Python - Build CSV"}
    namespace = {"json": json}
    exec("def render(item, results):\n    return " + prompt.code, namespace)
    result = namespace["render"]({"json": {"csv_text": "a;b"}}, {"Python - Build CSV": [{"json": {"summary": {"total": 2}}}]})
    assert '{"total":2}' in result
    assert "a;b" in result
    body = compile_expression(nodes["HTTP - Send email with ZIP"]["parameters"]["jsonBody"], node="Gmail", parameter="jsonBody")
    exec("def body(item):\n    return " + body.code, namespace)
    assert json.loads(namespace["body"]({"json": {"raw": "abc"}})) == {"raw": "abc"}


def test_strict_boolean_expression_distinguishes_numbers_and_strings():
    result = compile_expression("={{ $json.check === true }}", node="IF", parameter="condition")
    namespace = {}
    exec("def check(item):\n    return " + result.code, namespace)
    assert namespace["check"]({"json": {"check": True}}) is True
    assert namespace["check"]({"json": {"check": 1}}) is False
    assert namespace["check"]({"json": {"check": "true"}}) is False


def test_strict_comparisons_preserve_javascript_types():
    result = compile_expression("={{ $json.first === $json.second }}", node="IF", parameter="condition")
    namespace = {}
    exec("def check(item):\n    return " + result.code, namespace)
    assert namespace["check"]({"json": {"first": 1, "second": 1.0}}) is True
    assert namespace["check"]({"json": {"first": True, "second": 1}}) is False
    assert namespace["check"]({"json": {"first": {}, "second": {}}}) is False
    inverse = compile_expression("={{ $json.first !== 'a === b' }}", node="IF", parameter="condition")
    exec("def inverse(item):\n    return " + inverse.code, namespace)
    assert namespace["inverse"]({"json": {"first": "a === b"}}) is False
    with pytest.raises(WorkflowError, match="цепочка сравнений"):
        compile_expression("={{ $json.a === $json.b !== true }}", node="IF", parameter="condition")


def test_logical_operators_use_javascript_truth_and_short_circuit():
    result = compile_expression("={{ ($json.a || $json.b) && !$json.c }}", node="IF", parameter="condition")
    namespace = {}
    exec("def check(item):\n    return " + result.code, namespace)
    assert namespace["check"]({"json": {"a": [], "c": False}}) is True
    assert namespace["check"]({"json": {"a": 0, "b": "", "c": True}}) == ""
    assert namespace["check"]({"json": {"a": False, "b": 4, "c": True}}) is False
    comparison = compile_expression("={{ !$json.a === true }}", node="IF", parameter="condition")
    exec("def check(item):\n    return " + comparison.code, namespace)
    assert namespace["check"]({"json": {"a": False}}) is True
