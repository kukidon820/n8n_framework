"""Сгенерировать функцию передачи данных для узла n8n No Operation."""

from __future__ import annotations

import keyword
from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_noop_node(node: dict[str, Any], function_name: str) -> str:
    """Сгенерировать функцию, которая передаёт items дальше без изменений."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    if not isinstance(parameters, dict) or parameters:
        raise WorkflowError(f"Узел {name}: No Operation не поддерживает параметры")

    return f'''async def {function_name}(items):
    """Передать items дальше без изменений, как это делает No Operation в n8n."""
    return items
'''
