"""Проверки порядка выполнения узлов n8n.

Покрываются connections, циклы и ошибки графа, мешающие генерации.
"""

import pytest

from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError


def node(name):
    """Создать узел с минимальными полями, нужными построителю графа."""
    return {"id": name, "name": name, "type": "test", "parameters": {}}


def link(name, index=0, kind="main"):
    """Создать ссылку графа с каналом и номером входного порта."""
    return {"node": name, "type": kind, "index": index}


def test_model_connections_and_execution_order():
    names = ["Settings", "Python - Build CSV", "AI Agent - Analyze CSV",
             "DeepSeek Chat Model", "Edit Fields", "HTTP - Send email with ZIP"]
    workflow = {"nodes": [node(name) for name in names], "connections": {
        "Settings": {"main": [[link("Python - Build CSV")]]},
        "Python - Build CSV": {"main": [[link("AI Agent - Analyze CSV")]]},
        "DeepSeek Chat Model": {"ai_languageModel": [[link("AI Agent - Analyze CSV", kind="ai_languageModel")]]},
        "AI Agent - Analyze CSV": {"main": [[link("Edit Fields")]]},
        "Edit Fields": {"main": [[link("HTTP - Send email with ZIP")]]},
    }}
    edges = connections(workflow)
    assert any(e.source == "DeepSeek Chat Model" and e.target == "AI Agent - Analyze CSV"
               and e.channel == "ai_languageModel" for e in edges)
    order = execution_order(workflow)
    assert order.index("Python - Build CSV") < order.index("AI Agent - Analyze CSV")
    assert order.index("Edit Fields") < order.index("HTTP - Send email with ZIP")


def test_branches_and_input_indexes():
    workflow = {"nodes": [node(n) for n in "abcd"], "connections": {
        "a": {"main": [[link("b")], [link("c", 1)]]},
        "b": {"main": [[link("d")]]},
        "c": {"main": [[link("d", 1)]]},
    }}
    edges = connections(workflow)
    assert (edges[1].output, edges[1].input) == (1, 1)
    order = execution_order(workflow)
    assert order.index("d") > max(order.index("b"), order.index("c"))


def test_cycle_is_reported():
    workflow = {"nodes": [node("a"), node("b")], "connections": {
        "a": {"main": [[link("b")]]}, "b": {"main": [[link("a")]]}}}
    with pytest.raises(WorkflowError, match="Цикл"):
        execution_order(workflow)
