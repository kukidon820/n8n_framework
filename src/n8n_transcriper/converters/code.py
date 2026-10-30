"""Обёртка для исходного Python Code Node.

Конвертер переносит пользовательский алгоритм в функцию generated-проекта,
проверяя синтаксис и доступные переменные контекста. Само тело Python-кода
не переписывается и не запускается во время конвертации.
"""

import ast
import keyword
import textwrap
from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_code_node(node: dict[str, Any], function_name: str) -> str:
    """Сгенерировать функцию из Python Code Node и проверить её контекст.

    Для ``runOnceForAllItems`` исходный код получает весь список ``_items``.
    Для ``runOnceForEachItem`` тот же исходник помещается во внутреннюю функцию
    и вызывается по одному разу на каждый item.

    Args:
        node: Объект узла из workflow JSON.
        function_name: Безопасное Python-имя функции в generated-модуле.

    Returns:
        Исходный текст Python-модуля.

    Raises:
        WorkflowError: если узел не Python, режим или контекст не поддерживается,
            либо исходный код не компилируется.
    """
    name = node.get("name", "<без имени>")
    params = node.get("parameters", {})
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции {function_name!r}")
    if params.get("language", "javaScript") not in ("python", "pythonNative"):
        raise WorkflowError(f"Узел {name}: поддерживается только Python Code Node")
    code = params.get("pythonCode")
    if not isinstance(code, str) or not code.strip():
        raise WorkflowError(f"Узел {name}: параметр pythonCode пуст или отсутствует")
    mode = params.get("mode", "runOnceForAllItems")
    if mode not in ("runOnceForAllItems", "runOnceForEachItem"):
        raise WorkflowError(f"Узел {name}: неподдерживаемый режим {mode!r}")
    # Отступ добавляется только для вложения неизменённого исходника в функцию.
    body = textwrap.indent(code.rstrip(), "    ")
    source = f'def {function_name}(_items):\n    """Выполнить исходный Python Code Node для всех items."""\n{body}\n'
    if mode == "runOnceForEachItem":
        source = (
            f"def {function_name}(_items):\n"
            '    """Запустить исходный Python Code Node для каждого item."""\n'
            "    def run_item(_item):\n"
            '        """Выполнить исходное тело узла для одного item."""\n'
            f"{textwrap.indent(code.rstrip(), '        ')}\n"
            "    return [run_item(item) for item in _items]\n"
        )
    # Проверяем готовую обёртку Python-парсером до записи файлов на диск.
    try:
        tree = ast.parse(source)
        compile(tree, f"<узел {name}>", "exec")
    except SyntaxError as exc:
        raise WorkflowError(f"Узел {name}: ошибка Python-кода: {exc.msg}") from exc
    allowed = {"_items"} if mode == "runOnceForAllItems" else {"_item"}
    item_body = tree.body[0].body[1]
    code_tree = ast.Module(body=item_body.body[1:], type_ignores=[]) if mode == "runOnceForEachItem" else tree
    # Из исходника выявляется использование контекстных переменных, которых функция не получает.
    unsupported = sorted({n.id for n in ast.walk(code_tree) if isinstance(n, ast.Name)
                          and isinstance(n.ctx, ast.Load)
                          and n.id in {"_items", "_item", "_input", "_json", "_binary"}
                          and n.id not in allowed})
    if unsupported:
        raise WorkflowError(f"Узел {name}: неподдерживаемый контекст {', '.join(unsupported)}")
    return source
