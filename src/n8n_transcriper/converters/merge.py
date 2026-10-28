"""Генерация узла Merge в режиме последовательного добавления items."""

from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_merge_node(node: dict[str, Any], function_name: str) -> tuple[str, int]:
    name = node["name"]
    p = node["parameters"]
    version = node.get("typeVersion")
    if not isinstance(version, (int, float)) or isinstance(version, bool) or version < 3 or version >= 4:
        raise WorkflowError(f"Узел {name}: поддерживается Merge версии 3")
    if set(p) - {"mode", "numberInputs"} or p.get("mode", "append") != "append":
        raise WorkflowError(f"Узел {name}: поддерживается только Merge Append")
    count = p.get("numberInputs", 2)
    if not isinstance(count, int) or isinstance(count, bool) or not 2 <= count <= 10:
        raise WorkflowError(f"Узел {name}: некорректное число входов Merge")
    return f'''def {function_name}(inputs):
    result = []
    for items in inputs:
        result.extend(items)
    return result
''', count
