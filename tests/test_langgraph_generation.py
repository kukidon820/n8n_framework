"""Проверки генерации и запуска runner на LangGraph."""

from __future__ import annotations

import json
import subprocess
import sys

from n8n_transcriper.generator import generate_project


def _link(name: str, output: int = 0) -> dict[str, object]:
    return {"node": name, "type": "main", "index": output}


def _code_node(name: str, code: str) -> dict[str, object]:
    return {
        "id": name,
        "name": name,
        "type": "n8n-nodes-base.code",
        "parameters": {"language": "pythonNative", "pythonCode": code},
    }


def _run_generated(output, items, *, include_stdout=False):
    langgraph_package = output / "langgraph"
    langgraph_package.mkdir(exist_ok=True)
    (langgraph_package / "__init__.py").write_text("", encoding="utf-8")
    (langgraph_package / "graph.py").write_text(
        '''START = "__start__"
END = "__end__"

class StateGraph:
    def __init__(self, state_type):
        self.nodes = {}
        self.edges = []
    def add_node(self, name, action):
        self.nodes[name] = action
    def add_edge(self, source, target):
        self.edges.append((source, target))
    def compile(self):
        async def ainvoke(state):
            current = next(target for source, target in self.edges if source == START)
            while current != END:
                update = await self.nodes[current](state)
                state.update(update)
                current = next(target for source, target in self.edges if source == current)
            return state
        return type("CompiledGraph", (), {"ainvoke": staticmethod(ainvoke)})()
''',
        encoding="utf-8",
    )
    script = (
        "import json,logging,sys; "
        "sys.stdout.reconfigure(encoding='utf-8'); "
        "logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', stream=sys.stdout); "
        f"import main; print(json.dumps(main.run({items!r})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=output,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output_lines = [line for line in result.stdout.splitlines() if line.strip()]
    value = json.loads(output_lines[-1])
    return (value, result.stdout) if include_stdout else value


def test_default_generation_remains_direct_and_has_no_langgraph_dependency():
    workflow = {
        "nodes": [_code_node("Build", "return _items")],
        "connections": {},
    }

    files = generate_project(workflow)

    assert "StateGraph" not in files["main.py"]
    assert "langgraph" not in files["requirements.txt"]
    assert "workflow_runtime.py" not in files


def test_langgraph_mode_runs_each_n8n_node_as_a_graph_step(tmp_path):
    workflow = {
        "nodes": [
            {
                "id": "start",
                "name": "Start",
                "type": "n8n-nodes-base.manualTrigger",
                "parameters": {},
            },
            _code_node(
                "Build",
                'return [{"json": {"result": item["json"]["value"] + 1}} '
                "for item in _items]",
            ),
        ],
        "connections": {"Start": {"main": [[_link("Build")]]}},
    }
    output = tmp_path / "generated"

    files = generate_project(workflow, output, langgraph=True)

    assert "from langgraph.graph import END, START, StateGraph" in files["main.py"]
    assert "workflow_graph.ainvoke" in files["main.py"]
    assert "builder.add_node('step_1'" in files["main.py"]
    assert "langgraph>=1.0" in files["requirements.txt"]
    assert "workflow_runtime.py" not in files
    result, logs = _run_generated(
        output, [{"json": {"value": 4}}], include_stdout=True
    )
    assert result == [{"json": {"result": 5}}]
    assert "Workflow «без имени»: запуск" in logs
    assert "Узел «Build»: начат" in logs
    assert "Узел «Build»: завершён; выходных элементов: 1" in logs
    assert "Workflow завершён; выходных элементов: 1" in logs


def test_langgraph_graph_preserves_if_ports_and_joins_selected_branches(tmp_path):
    condition = {
        "combinator": "and",
        "conditions": [
            {
                "leftValue": "={{ $json.enabled }}",
                "rightValue": True,
                "operator": {"type": "boolean", "operation": "equals"},
            }
        ],
    }
    workflow = {
        "nodes": [
            {
                "id": "start",
                "name": "Start",
                "type": "n8n-nodes-base.manualTrigger",
                "parameters": {},
            },
            {
                "id": "if",
                "name": "Enabled?",
                "type": "n8n-nodes-base.if",
                "parameters": {"conditions": condition, "options": {}},
            },
            _code_node("True path", "return _items"),
            _code_node("False path", "return _items"),
            _code_node("Join", "return _items"),
        ],
        "connections": {
            "Start": {"main": [[_link("Enabled?")]]},
            "Enabled?": {"main": [[_link("True path")], [_link("False path")]]},
            "True path": {"main": [[_link("Join")]]},
            "False path": {"main": [[_link("Join")]]},
        },
    }
    output = tmp_path / "generated-branches"
    generate_project(workflow, output, langgraph=True)

    result = _run_generated(
        output,
        [{"json": {"enabled": True}}, {"json": {"enabled": False}}],
    )

    assert result == [
        {"json": {"enabled": True}},
        {"json": {"enabled": False}},
    ]


def test_langgraph_loop_mode_compiles_loop_body_as_nested_graph(tmp_path):
    workflow = {
        "nodes": [
            {
                "id": "start",
                "name": "Start",
                "type": "n8n-nodes-base.manualTrigger",
                "parameters": {},
            },
            _code_node(
                "Build",
                'return [{"json": {"value": value}} for value in range(5)]',
            ),
            {
                "id": "loop",
                "name": "Loop Items",
                "type": "n8n-nodes-base.splitInBatches",
                "typeVersion": 3,
                "parameters": {"batchSize": 2, "options": {"reset": False}},
            },
            _code_node(
                "Increment",
                'return [{"json": {"value": item["json"]["value"] + 1}} '
                "for item in _items]",
            ),
            _code_node("Finish", "return _items"),
        ],
        "connections": {
            "Start": {"main": [[_link("Build")]]},
            "Build": {"main": [[_link("Loop Items")]]},
            "Loop Items": {"main": [[_link("Finish")], [_link("Increment")]]},
            "Increment": {"main": [[_link("Loop Items")]]},
        },
    }
    output = tmp_path / "generated-loop"

    files = generate_project(workflow, output, langgraph=True)

    assert "loop_body_graph = _build_loop_body_graph()" in files["main.py"]
    assert "builder.add_node('body_step_" in files["main.py"]
    assert "workflow_runtime.py" not in files
    assert _run_generated(output, None) == [
        {"json": {"value": 1}},
        {"json": {"value": 2}},
        {"json": {"value": 3}},
        {"json": {"value": 4}},
        {"json": {"value": 5}},
    ]
