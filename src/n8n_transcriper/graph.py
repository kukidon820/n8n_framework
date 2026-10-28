"""Связи между узлами и порядок исполнения workflow."""

from collections import deque
from dataclasses import dataclass
from typing import Any

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


def execution_order(workflow: dict[str, Any]) -> list[str]:
    """Определить порядок основных узлов, сохраняя порядок исходного списка.

    Вспомогательные AI-связи не являются последовательными переходами и
    возвращаются отдельно через connections(). Циклы нельзя свести к цепочке.
    """
    edges = [edge for edge in connections(workflow) if edge.channel == "main"]
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
