"""Поддержка настроек и runtime-класса Loop Over Items."""

from dataclasses import dataclass
from typing import Any

from n8n_transcriper.reader import WorkflowError


@dataclass(frozen=True)
class LoopSettings:
    """Настройки узла и индексы его выходов в конкретной версии n8n."""

    version: int
    batch_size: int
    reset: bool
    loop_output: int
    done_output: int | None
    output_count: int


def loop_output_indices(node: dict[str, Any]) -> tuple[int, int | None, int]:
    """Вернуть индексы loop/done выходов для версии Split In Batches."""
    version = node.get("typeVersion", 3)
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version < 1:
        raise WorkflowError(f"Узел {node.get('name', '<без имени>')}: некорректная версия Loop Over Items {version!r}")
    version = int(version)
    if version == 1:
        return 0, None, 1
    if version == 2:
        return 0, 1, 2
    if version == 3:
        return 1, 0, 2
    raise WorkflowError(f"Узел {node.get('name', '<без имени>')}: версия Loop Over Items {version} не поддерживается")


def generate_loop_node(node: dict[str, Any]) -> LoopSettings:
    """Проверить фиксированные параметры Loop Over Items."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    loop_output, done_output, output_count = loop_output_indices(node)
    version = int(node.get("typeVersion", 3))
    if not isinstance(parameters, dict) or set(parameters) - {"batchSize", "options"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые параметры Loop Over Items")

    default_size = 1 if version == 3 else 10
    batch_size = parameters.get("batchSize", default_size)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise WorkflowError(f"Узел {name}: batchSize должен быть целым числом не меньше 1")

    options = parameters.get("options", {})
    if not isinstance(options, dict) or set(options) - {"reset"}:
        raise WorkflowError(f"Узел {name}: поддерживается только настройка options.reset")
    reset = options.get("reset", False)
    if not isinstance(reset, bool):
        raise WorkflowError(f"Узел {name}: options.reset должен быть фиксированным логическим значением")

    return LoopSettings(version, batch_size, reset, loop_output, done_output, output_count)


LOOP_MODULE = '''"""Runtime-класс для узла n8n Loop Over Items."""

from copy import deepcopy


class Loop:
    """Разбивать items на пакеты и собирать результаты ветви loop."""

    def __init__(self, batch_size=1, reset=False):
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size должен быть целым числом не меньше 1")
        if not isinstance(reset, bool):
            raise TypeError("reset должен быть логическим значением")
        self.batch_size = batch_size
        self.reset = reset
        self.done_items = []
        self.completed = False
        self.results = {}
        self._feedback = None
        self._linked_item_index = 0

    def iterate(self, items):
        """Выдавать очередной пакет; обратная связь подаётся через collect()."""
        current_items = deepcopy(list(items))
        self.done_items = []
        self.completed = not current_items
        self.results = {}
        self._feedback = None
        self._linked_item_index = 0
        if not current_items:
            return

        if self.reset:
            while current_items:
                batch = deepcopy(current_items[:self.batch_size])
                self.done_items = []
                self._feedback = None
                yield batch
                if self._feedback is None:
                    raise RuntimeError("Loop.collect() не вызван после обработки пакета")
                if not self._feedback:
                    self.completed = True
                    return
                current_items = deepcopy(self._feedback)
            self.completed = True
            return

        offset = 0
        while offset < len(current_items):
            batch = deepcopy(current_items[offset:offset + self.batch_size])
            self._feedback = None
            yield batch
            if self._feedback is None:
                raise RuntimeError("Loop.collect() не вызван после обработки пакета")
            self.done_items.extend(deepcopy(self._feedback))
            offset += self.batch_size
        self.completed = True

    def collect(self, items):
        """Передать результат узлов тела цикла для следующей итерации."""
        self._feedback = deepcopy(list(items))

    def record_result(self, node_name, items):
        """Сохранить результаты узла тела цикла за все итерации."""
        self.results.setdefault(node_name, []).extend(items)
        return self.results[node_name]

    def link_batch(self, node_name, items):
        """Проставить стабильные индексы для n8n paired-item ссылок."""
        for offset, item in enumerate(items):
            lineage = dict(item.get("_n8n_lineage", {}))
            lineage[node_name] = self._linked_item_index + offset
            item["_n8n_lineage"] = lineage
        self._linked_item_index += len(items)
'''
