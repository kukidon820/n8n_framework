"""Преобразование n8n connections в связи и порядок генерации узлов.

Модуль не исполняет workflow. Он строит упорядоченный список для генератора,
сохраняя номера входов и выходов и отделяя служебные AI-связи от main-потока.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any

from n8n_transcriper.converters.loop import loop_output_indices
from n8n_transcriper.reader import WorkflowError, validate_workflow


@dataclass(frozen=True)
class Connection:
    """Одно ребро графа n8n.

    ``output`` — индекс выхода исходного узла, а ``input`` — индекс входа
    целевого. ``channel`` сохраняет тип связи, например ``main`` или
    ``ai_languageModel``.
    """

    source: str
    target: str
    channel: str
    output: int
    input: int
    target_type: str


def connections(workflow: dict[str, Any]) -> list[Connection]:
    """Развернуть вложенный формат ``connections`` в плоский список.

    Перед чтением выполняется структурная проверка. Каждый элемент результата
    представляет одно ребро и сохраняет канал, индексы портов и тип получателя.
    """
    validate_workflow(workflow)
    # Индекс внешнего списка — номер выхода исходного узла.
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
    """Найти рёбра, которые замыкают тело Loop Over Items на его вход.

    Поиск начинается с выхода ``loop`` конкретной версии узла и проходит по
    main-связям. Найденные рёбра исключаются из топологической сортировки, но
    сохраняются для генерации цикла.
    """
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
    """Вернуть обратные рёбра Loop Over Items из проверенного workflow."""
    return _loop_feedback_edges(workflow, connections(workflow))


def execution_order(workflow: dict[str, Any]) -> list[str]:
    """Вернуть топологический порядок узлов основного потока.

    Соседние узлы обрабатываются в порядке исходного JSON, если зависимости
    допускают такой порядок. AI-соединения не влияют на порядок, а распознанная
    обратная связь Loop удаляется только на время сортировки.

    Raises:
        WorkflowError: если после удаления обратных связей Loop в main-графе
            остаётся цикл.
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
    # Алгоритм Кана: в очередь попадают узлы, у которых больше нет родителей.
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
