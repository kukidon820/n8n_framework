"""Проверки компиляции n8n expressions в Python.

Покрываются ссылки на items, шаблоны текста и диагностика неподдерживаемого синтаксиса.
"""

import pytest
import json

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def test_named_node_and_nested_paths():
    result = compile_expression("={{ $('Settings').first().json.mail_to }}", node="Edit", parameter="value")
    assert result.code == "results['Settings'][0]['json']['mail_to']"
    assert result.dependencies == {"Settings"}


def test_number_conversion_and_named_node_path():
    result = compile_expression(
        "={{ Number($('Python - Build CSV').first().json.summary.pending) }}",
        node="IF",
        parameter="leftValue",
    )
    assert result.dependencies == {"Python - Build CSV"}
    assert "float(value)" in result.code


def test_named_node_linked_item_selector():
    result = compile_expression("={{ $('Build').item.json.rows[0].id }}", node="Edit", parameter="value")
    assert result.code == "results['Build'][item['_n8n_lineage']['Build']]['json']['rows'][0]['id']"
    assert result.dependencies == {"Build"}


def test_template_and_array():
    result = compile_expression("ID: {{ $json.data[0].id }}", node="test", parameter="text")
    assert result.code == "'ID: ' + str(item['json']['data'][0]['id']) + ''"


def test_unsupported_javascript_does_not_silently_convert():
    with pytest.raises(WorkflowError, match="узел|Узел"):
        compile_expression("={{ $json.id + 1 }}", node="test", parameter="value")


def test_real_prompt_and_gmail_body():
    nodes = {
        "AI Agent - Analyze CSV": {"parameters": {
            "text": "=Stats {{ JSON.stringify($('Python - Build CSV').first().json.summary) }} CSV {{ $json.csv_text }}",
        }},
        "HTTP - Send email with ZIP": {"parameters": {
            "jsonBody": "={{ JSON.stringify({raw: $json.raw}) }}",
        }},
    }
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


def test_json_body_object_with_pretty_named_node_stringify():
    expression = '''={{
      {
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "messages": [
          {
            "role": "system",
            "content": "Ты полезный ассистент, который анализирует показатели МКС."
          },
          {
            "role": "user",
            "content": "Привет! Объясни показатели.\\n\\nДанные:\\n" +
              JSON.stringify($('HTTP Request1').first().json, null, 2)
          }
        ]
      }
    }}'''
    result = compile_expression(expression, node="HTTP Request2", parameter="jsonBody")
    assert result.dependencies == {"HTTP Request1"}

    namespace = {"json": json}
    exec("def body(results):\n    return " + result.code, namespace)
    body = namespace["body"]({"HTTP Request1": [{"json": {
        "latitude": 45.0,
        "longitude": -72.5,
        "name": "МКС",
    }}]})

    assert body["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"].endswith(
        '{\n  "latitude": 45.0,\n  "longitude": -72.5,\n  "name": "МКС"\n}'
    )


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


@pytest.mark.parametrize(
    "expression,first,second,expected",
    [
        ("$json.first == $json.second", 1, "1", True),
        ("$json.first == $json.second", False, "", True),
        ("$json.first == $json.second", 1, "text", False),
        ("$json.first != $json.second", 2, "3", True),
        ("$json.first == 'value {left}'", "value {left}", "ignored", True),
    ],
)
def test_loose_comparisons(expression, first, second, expected):
    result = compile_expression(f"={{{{ {expression} }}}}", node="IF", parameter="condition")
    namespace = {}
    exec("def check(item):\n    return " + result.code, namespace)
    assert namespace["check"]({"json": {"first": first, "second": second}}) is expected


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
