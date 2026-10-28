"""Генерация ручных назначений Edit Fields для n8n Set 3.3+."""

import keyword
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def generate_edit_fields(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Создать модуль узла и вернуть имена узлов, на которые он ссылается."""
    name = node.get("name", "<без имени>")
    p = node.get("parameters", {})
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    version = node.get("typeVersion")
    if not isinstance(version, (int, float)) or isinstance(version, bool) or not 3.3 <= version <= 3.5:
        raise WorkflowError(f"Узел {name}: неподдерживаемая версия Set {version!r}")
    if set(p) - {"mode", "assignments", "includeOtherFields", "include", "options"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые параметры Edit Fields")
    if p.get("mode", "manual") != "manual" or p.get("include", "all") != "all":
        raise WorkflowError(f"Узел {name}: поддерживается только ручное назначение и включение всех полей")
    include = p.get("includeOtherFields", False)
    options = p.get("options", {})
    if not isinstance(include, bool) or not isinstance(options, dict) or set(options) - {"stripBinary", "includeBinary", "dotNotation"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки Edit Fields")
    for option, value in options.items():
        if not isinstance(value, bool):
            raise WorkflowError(f"Узел {name}: настройка {option} должна быть логической")
    if version >= 3.4 and "includeBinary" in options or version < 3.4 and "stripBinary" in options:
        raise WorkflowError(f"Узел {name}: настройка binary не поддерживается этой версией")
    keep_binary = include and not options.get("stripBinary", True) if version >= 3.4 else options.get("includeBinary", True)
    assignments = p.get("assignments", {})
    if not isinstance(assignments, dict) or set(assignments) - {"assignments"} or not isinstance(assignments.get("assignments", []), list):
        raise WorkflowError(f"Узел {name}: некорректные assignments")
    lines = []
    dependencies: set[str] = set()
    for assignment in assignments.get("assignments", []):
        if not isinstance(assignment, dict) or set(assignment) - {"id", "name", "value", "type"}:
            raise WorkflowError(f"Узел {name}: некорректное назначение")
        field = assignment.get("name")
        value = assignment.get("value")
        if not isinstance(field, str) or not field or (options.get("dotNotation", True) and ("." in field or "[" in field)):
            raise WorkflowError(f"Узел {name}: неподдерживаемое имя поля {field!r}")
        if assignment.get("type") != "string" or not isinstance(value, str):
            raise WorkflowError(f"Узел {name}, поле {field}: пока поддерживаются только строковые значения")
        expression = compile_expression(value, node=name, parameter=f"assignments.{field}")
        dependencies.update(expression.dependencies)
        lines.extend([f"        value = {expression.code}",
                      f"        if not isinstance(value, str):\n            raise TypeError({('Узел ' + name + ', поле ' + field + ': ожидалась строка')!r})",
                      f"        data[{field!r}] = value"])
    source = ('import json\nfrom copy import deepcopy\n\n\n' + f'def {function_name}(items, results, execution):\n' +
              '    result = []\n    for item in items:\n' +
              f'        data = deepcopy(item.get("json", {{}})) if {include!r} else {{}}\n' +
              ('\n'.join(lines) + '\n' if lines else '') +
              '        output = {"json": data}\n' +
              f'        if {keep_binary!r} and "binary" in item:\n            output["binary"] = dict(item["binary"])\n' +
              '        result.append(output)\n    return result\n')
    return source, dependencies
