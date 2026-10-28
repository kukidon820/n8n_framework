from pathlib import Path

import pytest

from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError, read_workflow


def node(name):
    return {"id": name, "name": name, "type": "test", "parameters": {}}


def link(name, index=0, kind="main"):
    return {"node": name, "type": kind, "index": index}


def test_real_workflow_connections():
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    workflow = read_workflow(path)
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
