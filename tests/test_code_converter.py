import pytest

from n8n_transcriper.converters.code import generate_code_node
from n8n_transcriper.reader import WorkflowError


def make_node(code, **parameters):
    return {"name": "Python", "parameters": {"language": "pythonNative", "pythonCode": code, **parameters}}


def test_all_items_preserves_original_code():
    code = 'import json\nreturn [{"json": {"count": len(_items), "text": json.dumps(_items)}}]'
    source = generate_code_node(make_node(code), "build")
    namespace = {}
    exec(source, namespace)
    assert namespace["build"]([{"json": {"value": 1}}])[0]["json"]["count"] == 1
    assert "import json" in source


def test_each_item_executes_independently():
    node = make_node('return {"json": {"value": _item["json"]["value"] * 2}}', mode="runOnceForEachItem")
    namespace = {}
    exec(generate_code_node(node, "build"), namespace)
    assert namespace["build"]([{"json": {"value": 2}}, {"json": {"value": 3}}]) == [
        {"json": {"value": 4}}, {"json": {"value": 6}}]


def test_invalid_or_unsupported_code_is_reported():
    with pytest.raises(WorkflowError, match="Python"):
        generate_code_node({"name": "JS", "parameters": {}}, "run")
    with pytest.raises(WorkflowError, match="контекст"):
        generate_code_node(make_node("return _item"), "run")
    with pytest.raises(WorkflowError, match="контекст"):
        generate_code_node(make_node("return _items", mode="runOnceForEachItem"), "run")
    with pytest.raises(WorkflowError, match="ошибка Python"):
        generate_code_node(make_node("if:"), "run")
