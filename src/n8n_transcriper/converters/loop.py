"""Настройки n8n Loop Over Items и код его лёгкого generated-runtime.

Конвертер определяет выходы по версии узла и проверяет batchSize, reset и
общие настройки узла. Строка
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
    execute_once: bool
    retry_on_fail: bool
    max_tries: int
    wait_between_tries: int | float
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
    if isinstance(version, bool) or not isinstance(version, (int, float)):
        version = 3
    version = int(version)
    if version < 1:
        version = 1
    elif version > 3:
        version = 3
    if version == 1:
        return 0, None, 1
    if version == 2:
        return 0, 1, 2
    if version == 3:
        return 1, 0, 2
    return 1, 0, 2


def generate_loop_node(node: dict[str, Any]) -> LoopSettings:
    """Проверить поддерживаемые параметры узла и вернуть LoopSettings.

    Если размер пакета не задан, используются значения по умолчанию n8n для
    основной версии. reset и executeOnce принимают только фиксированные bool.
    retryOnFail повторяет внутреннюю обработку Loop, но не шаги его тела.
    """
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    loop_output, done_output, output_count = loop_output_indices(node)
    raw_version = node.get("typeVersion", 3)
    version = int(raw_version) if isinstance(raw_version, (int, float)) and not isinstance(raw_version, bool) else 3
    version = min(max(version, 1), 3)
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

    execute_once = node.get("executeOnce", False)
    if not isinstance(execute_once, bool):
        raise WorkflowError(f"Узел {name}: executeOnce должен быть логическим значением")

    retry_on_fail = node.get("retryOnFail", False)
    if not isinstance(retry_on_fail, bool):
        raise WorkflowError(f"Узел {name}: retryOnFail должен быть логическим значением")
    max_tries = node.get("maxTries", 3) if retry_on_fail else 1
    wait_between_tries = node.get("waitBetweenTries", 1000) if retry_on_fail else 0
    if not isinstance(max_tries, int) or isinstance(max_tries, bool) or max_tries < 1:
        raise WorkflowError(f"Узел {name}: maxTries должен быть целым числом не меньше 1")
    if (
        not isinstance(wait_between_tries, (int, float))
        or isinstance(wait_between_tries, bool)
        or wait_between_tries < 0
    ):
        raise WorkflowError(f"Узел {name}: waitBetweenTries должен быть числом не меньше 0")

    return LoopSettings(
        version,
        batch_size,
        reset,
        execute_once,
        retry_on_fail,
        max_tries,
        wait_between_tries,
        loop_output,
        done_output,
        output_count,
    )


LOOP_MODULE = '''"""Runtime-класс для пакетной обработки items в Loop Over Items.

Объект используется только сгенерированным main.py. Метод iterate отдаёт очередной
пакет, а collect принимает данные обратной связи от последнего узла тела цикла.
"""

import time
from copy import deepcopy


class Loop:
    """Выдавать batches и хранить результаты выполненных итераций.

    Args:
        batch_size: Максимум входных items в одном вызове тела цикла.
        reset: Если True, считать данные обратной связи новым набором для обработки;
            пустая обратная связь завершает итерации.
        execute_once: Передавать циклу только первый item при каждом входе.
        retry_on_fail: Повторять ошибки подготовки входа, пакета и обратной связи.
        max_tries: Максимум попыток при включённом retry_on_fail.
        wait_between_tries: Пауза между попытками в миллисекундах.

    ``done_items`` содержит обработанные items для Done-выхода. ``results`` хранит
    накопленные выходы именованных узлов, нужные expressions внутри цикла.
    """

    def __init__(
        self,
        batch_size=1,
        reset=False,
        execute_once=False,
        retry_on_fail=False,
        max_tries=3,
        wait_between_tries=1000,
        n8n_manual_parameters=None,
    ):
        """Сохранить настройки и подготовить пустое состояние текущего запуска.

        ``n8n_manual_parameters`` содержит шаблон настроек узла, версия которого
        использовала fallback; заполните значения и реализуйте эти настройки
        вручную, если они отличаются от известного поведения.
        """
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size должен быть целым числом не меньше 1")
        if not isinstance(reset, bool):
            raise TypeError("reset должен быть логическим значением")
        if not isinstance(execute_once, bool):
            raise TypeError("execute_once должен быть логическим значением")
        if not isinstance(retry_on_fail, bool):
            raise TypeError("retry_on_fail должен быть логическим значением")
        if not isinstance(max_tries, int) or isinstance(max_tries, bool) or max_tries < 1:
            raise ValueError("max_tries должен быть целым числом не меньше 1")
        if (
            not isinstance(wait_between_tries, (int, float))
            or isinstance(wait_between_tries, bool)
            or wait_between_tries < 0
        ):
            raise ValueError("wait_between_tries должен быть числом не меньше 0")
        unsupported_paths = (
            n8n_manual_parameters.get("unsupported_parameter_paths", ())
            if isinstance(n8n_manual_parameters, dict)
            else ()
        )
        if unsupported_paths:
            paths = ", ".join(str(path) for path in unsupported_paths)
            raise NotImplementedError(
                f"Параметры Loop Over Items требуют ручной реализации: {paths}"
            )
        self.batch_size = batch_size
        self.reset = reset
        self.execute_once = execute_once
        self.retry_on_fail = retry_on_fail
        self.max_tries = max_tries
        self.wait_between_tries = wait_between_tries
        # TODO: вручную реализовать настройки, перечисленные в n8n_manual_parameters,
        # если исходная версия Loop отличается от выбранного fallback.
        self.n8n_manual_parameters = n8n_manual_parameters
        self.done_items = []
        self.completed = False
        self.results = {}
        self._feedback = None
        self._linked_item_index = 0

    def _retry(self, action):
        """Повторить внутренний шаг Loop по настройкам Retry On Fail."""
        attempts = self.max_tries if self.retry_on_fail else 1
        for attempt in range(attempts):
            try:
                return action()
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                if self.wait_between_tries:
                    time.sleep(self.wait_between_tries / 1000)

    def iterate(self, items):
        """Последовательно выдавать пакеты, ожидая collect после каждого.

        При обычном режиме пакет выбирается по смещению во входном списке, а
        выход тела цикла добавляется в ``done_items``. При reset=True новый
        набор берётся из обратной связи, поэтому пустой результат останавливает
        цикл. Метод — генератор: тело выполняется между ``yield`` и ``collect``.
        """
        source_items = list(items)
        if self.execute_once:
            source_items = source_items[:1]
        current_items = self._retry(lambda: deepcopy(source_items))
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
                batch = self._retry(lambda: deepcopy(current_items[:self.batch_size]))
                self.done_items = []
                self._feedback = None
                yield batch
                if self._feedback is None:
                    raise RuntimeError("Loop.collect() не вызван после обработки пакета")
                if not self._feedback:
                    self.completed = True
                    return
                current_items = self._retry(lambda: deepcopy(self._feedback))
            self.completed = True
            return

        # В обычном режиме обратная связь возвращает результаты, но не меняет
        # оставшуюся часть исходного набора: следующий пакет выбирается по offset.
        offset = 0
        while offset < len(current_items):
            batch = self._retry(lambda: deepcopy(current_items[offset:offset + self.batch_size]))
            self._feedback = None
            yield batch
            if self._feedback is None:
                raise RuntimeError("Loop.collect() не вызван после обработки пакета")
            self.done_items.extend(deepcopy(self._feedback))
            offset += self.batch_size
        self.completed = True

    def collect(self, items):
        """Передать выход обратной связи после выполнения текущего пакета."""
        source_items = list(items)
        if self.execute_once:
            source_items = source_items[:1]
        self._feedback = self._retry(lambda: deepcopy(source_items))

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
