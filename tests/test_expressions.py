import pytest

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


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
