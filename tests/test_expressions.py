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
