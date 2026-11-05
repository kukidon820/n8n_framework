"""Проверки сообщений выполнения в созданных Python-проектах."""

from __future__ import annotations

import subprocess
import sys

import pytest

from n8n_transcriper.generator import generate_project


def _workflow(code: str) -> dict:
    return {
        "name": "Проверка логов",
        "nodes": [
            {
                "id": "code-1",
                "name": "Сбор данных",
                "type": "n8n-nodes-base.code",
                "parameters": {"language": "pythonNative", "pythonCode": code},
            }
        ],
        "connections": {},
    }


def _install_fake_langgraph(project) -> None:
    package = project / "langgraph"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "graph.py").write_text(
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
                state.update(await self.nodes[current](state))
                current = next(target for source, target in self.edges if source == current)
            return state
        return type("CompiledGraph", (), {"ainvoke": staticmethod(ainvoke)})()
''',
        encoding="utf-8",
    )


@pytest.mark.parametrize("langgraph", [False, True], ids=["python", "langgraph"])
def test_generated_main_logs_workflow_and_node_success(tmp_path, langgraph):
    project = tmp_path / "generated"
    generate_project(
        _workflow('return [{"json": {"result": 42}}]'),
        project,
        langgraph=langgraph,
    )
    if langgraph:
        _install_fake_langgraph(project)

    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Workflow «Проверка логов»: запуск" in result.stdout
    assert "Узел «Сбор данных»: начат" in result.stdout
    assert "Узел «Сбор данных»: завершён; выходных элементов: 1" in result.stdout
    assert "Workflow завершён; выходных элементов: 1" in result.stdout


@pytest.mark.parametrize("langgraph", [False, True], ids=["python", "langgraph"])
def test_generated_main_logs_node_name_and_error(tmp_path, langgraph):
    project = tmp_path / "generated"
    generate_project(_workflow('raise ValueError("ошибка тестового узла")'), project, langgraph=langgraph)
    if langgraph:
        _install_fake_langgraph(project)

    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "Узел «Сбор данных»: ошибка выполнения" in output
    assert "ошибка тестового узла" in output
    assert "Workflow остановлен с ошибкой" in output
