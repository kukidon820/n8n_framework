"""Генерация функции из исходного Python-кода узла n8n."""

import ast
import keyword
import textwrap
from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_code_node(node: dict[str, Any], function_name: str) -> str:
    """Обернуть исходный код в функцию для одного или всех входных items."""
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
    body = textwrap.indent(code.rstrip(), "    ")
    source = f"def {function_name}(_items):\n{body}\n"
    if mode == "runOnceForEachItem":
        source = (
            f"def {function_name}(_items):\n"
            "    def run_item(_item):\n"
            f"{textwrap.indent(code.rstrip(), '        ')}\n"
            "    return [run_item(item) for item in _items]\n"
        )
    try:
        tree = ast.parse(source)
        compile(tree, f"<узел {name}>", "exec")
    except SyntaxError as exc:
        raise WorkflowError(f"Узел {name}: ошибка Python-кода: {exc.msg}") from exc
    allowed = {"_items"} if mode == "runOnceForAllItems" else {"_item"}
    code_tree = tree.body[0].body[0] if mode == "runOnceForEachItem" else tree
    unsupported = sorted({n.id for n in ast.walk(code_tree) if isinstance(n, ast.Name)
                          and isinstance(n.ctx, ast.Load)
                          and n.id in {"_items", "_item", "_input", "_json", "_binary"}
                          and n.id not in allowed})
    if unsupported:
        raise WorkflowError(f"Узел {name}: неподдерживаемый контекст {', '.join(unsupported)}")
    return source
