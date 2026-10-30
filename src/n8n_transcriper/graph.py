"""Связи между узлами и порядок исполнения workflow."""

from collections import deque
from dataclasses import dataclass
from typing import Any

from n8n_transcriper.converters.loop import loop_output_indices
from n8n_transcriper.reader import WorkflowError, validate_workflow


@dataclass(frozen=True)
class Connection:
    """Связь с индексами выхода источника и входа получателя."""

    source: str
    target: str
    channel: str
    output: int
    input: int
    target_type: str


def connections(workflow: dict[str, Any]) -> list[Connection]:
    """Вернуть все связи, включая подключения моделей и инструментов."""
    validate_workflow(workflow)
    return [
        Connection(source, target["node"], channel, output_index,
                   target["index"], target["type"])
        for source, channels in workflow["connections"].items()
        for channel, outputs in channels.items()
        for output_index, targets in enumerate(outputs)
        for target in targets
    ]


def _loop_feedback_edges(
    workflow: dict[str, Any], edges: list[Connection]
) -> list[Connection]:
    """Найти соединения из тела Loop Over Items обратно в сам узел."""
    main_edges = [edge for edge in edges if edge.channel == "main"]
    outgoing: dict[str, list[Connection]] = {}
    for edge in main_edges:
        outgoing.setdefault(edge.source, []).append(edge)

    feedback = []
    for node in workflow["nodes"]:
        if node["type"] != "n8n-nodes-base.splitInBatches":
            continue
        loop_output, _, _ = loop_output_indices(node)
        name = node["name"]
        starts = [edge.target for edge in outgoing.get(name, []) if edge.output == loop_output]
        visited: set[str] = set()
        pending = list(starts)
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for edge in outgoing.get(current, []):
                if edge.target == name:
                    feedback.append(edge)
                elif edge.target not in visited:
                    pending.append(edge.target)
    return feedback


def loop_feedback_edges(workflow: dict[str, Any]) -> list[Connection]:
    """Вернуть обратные связи цикла, исключаемые из топологического порядка."""
    return _loop_feedback_edges(workflow, connections(workflow))


def execution_order(workflow: dict[str, Any]) -> list[str]:
    """Определить порядок основных узлов, сохраняя порядок исходного списка.

    Вспомогательные AI-связи не являются последовательными переходами. Единственная
    распознаваемая обратная связь — возврат из тела Loop Over Items в сам Loop.
    """
    all_edges = connections(workflow)
    loop_feedback = set(_loop_feedback_edges(workflow, all_edges))
    edges = [edge for edge in all_edges if edge.channel == "main" and edge not in loop_feedback]
    names = [node["name"] for node in workflow["nodes"]]
    incoming = dict.fromkeys(names, 0)
    outgoing: dict[str, list[str]] = {name: [] for name in names}
    for edge in edges:
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = deque(name for name in names if incoming[name] == 0)
    result = []
    while ready:
        current = ready.popleft()
        result.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(result) != len(names):
        remaining = [name for name in names if incoming[name]]
        raise WorkflowError(f"Цикл в основных соединениях: {', '.join(remaining)}")
    return result
