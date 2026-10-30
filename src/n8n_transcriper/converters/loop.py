"""Настройки n8n Loop Over Items и код его лёгкого generated-runtime.

Конвертер определяет выходы по версии узла и проверяет batchSize/reset. Строка
``LOOP_MODULE`` записывается только в generated-проект, если workflow содержит
цикл; обычные workflow не получают дополнительный runtime-файл.
"""

from dataclasses import dataclass
from typing import Any

from n8n_transcriper.reader import WorkflowError


@dataclass(frozen=True)
class LoopSettings:
    """Проверенные настройки цикла в формате, удобном генератору.

    ``loop_output`` и ``done_output`` — индексы выходов main-графа. В версии 1
    у узла нет отдельного Done-порта, поэтому ``done_output`` равен ``None``.
    """

    version: int
    batch_size: int
    reset: bool
    loop_output: int
    done_output: int | None
    output_count: int


def loop_output_indices(node: dict[str, Any]) -> tuple[int, int | None, int]:
    """Определить номера выходов Loop и Done для версии узла n8n.

    Версия 1 имеет только Loop output; в версии 2 порядок — Loop, Done, а в
    версии 3 — Done, Loop. Дробная minor-версия (например, 3.2) относится к
    соответствующей поддерживаемой основной версии.
    """
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
    """Проверить поддерживаемые параметры узла и вернуть LoopSettings.

    Если размер пакета не задан, используются значения по умолчанию n8n для
    основной версии. Выражения для ``reset`` не преобразуются: настройка должна
    быть фиксированным bool, чтобы поведение generated-класса было явным.
    """
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


LOOP_MODULE = '''"""Runtime-класс для пакетной обработки items в Loop Over Items.

Объект используется только сгенерированным main.py. Метод iterate отдаёт очередной
пакет, а collect принимает данные обратной связи от последнего узла тела цикла.
"""

from copy import deepcopy


class Loop:
    """Выдавать batches и хранить результаты выполненных итераций.

    Args:
        batch_size: Максимум входных items в одном вызове тела цикла.
        reset: Если True, считать данные обратной связи новым набором для обработки;
            пустая обратная связь завершает итерации.

    ``done_items`` содержит обработанные items для Done-выхода. ``results`` хранит
    накопленные выходы именованных узлов, нужные expressions внутри цикла.
    """

    def __init__(self, batch_size=1, reset=False):
        """Сохранить настройки и подготовить пустое состояние текущего запуска."""
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
        """Последовательно выдавать пакеты, ожидая collect после каждого.

        При обычном режиме пакет выбирается по смещению во входном списке, а
        выход тела цикла добавляется в ``done_items``. При reset=True новый
        набор берётся из обратной связи, поэтому пустой результат останавливает
        цикл. Метод — генератор: тело выполняется между ``yield`` и ``collect``.
        """
        current_items = deepcopy(list(items))
        self.done_items = []
        self.completed = not current_items
        self.results = {}
        self._feedback = None
        self._linked_item_index = 0
        if not current_items:
            return

        # Reset трактует каждый вернувшийся набор как новое входное множество.
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

        # В обычном режиме обратная связь возвращает результаты, но не меняет
        # оставшуюся часть исходного набора: следующий пакет выбирается по offset.
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
        """Передать выход обратной связи после выполнения текущего пакета."""
        self._feedback = deepcopy(list(items))

    def record_result(self, node_name, items):
        """Добавить выход узла тела цикла к результатам прошлых итераций.

        Возвращаемый список становится значением ``results[node_name]`` в main.py.
        """
        self.results.setdefault(node_name, []).extend(items)
        return self.results[node_name]

    def link_batch(self, node_name, items):
        """Проставить глобальные индексы items Loop для ссылок ``.item``.

        Индекс продолжает предыдущую пачку, чтобы накопленные результаты узлов
        могли однозначно разрешить linked-item expression.
        """
        for offset, item in enumerate(items):
            lineage = dict(item.get("_n8n_lineage", {}))
            lineage[node_name] = self._linked_item_index + offset
            item["_n8n_lineage"] = lineage
        self._linked_item_index += len(items)
'''
