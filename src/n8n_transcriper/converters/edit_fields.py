"""Генерация назначений и выборки полей для n8n Edit Fields / Set.

Значения expressions преобразуются заранее, а generated-функция применяет их
к каждому входному item. Поля JSON и binary сохраняются согласно параметрам узла.
"""

import keyword
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def generate_edit_fields(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Сгенерировать функцию ручного Edit Fields и собрать ссылки на другие узлы.

    Версия Set используется только для значений по умолчанию и старого режима
    выбора входных полей. Известные параметры обрабатываются по их наличию и
    типу, даже если они пришли из другой версии узла.

    Returns:
        Исходник модуля и имена узлов, используемых в expressions.
    """
    name = node.get("name", "<без имени>")
    p = node.get("parameters", {})
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    source_version = node.get("_n8nOriginalTypeVersion", node.get("typeVersion"))
    version = source_version
    if not isinstance(version, (int, float)) or isinstance(version, bool):
        version = 3.5
    elif version < 3.3:
        version = 3.3
    elif version > 3.5:
        version = 3.5
    if set(p) - {"mode", "assignments", "includeOtherFields", "include", "includeFields", "excludeFields", "options"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые параметры Edit Fields")
    if p.get("mode", "manual") != "manual" or p.get("include", "all") not in {"all", "none", "selected", "except"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемый режим Edit Fields")
    include = p.get("includeOtherFields", False)
    options = p.get("options", {})
    if not isinstance(include, bool) or not isinstance(options, dict) or set(options) - {
        "stripBinary", "includeBinary", "dotNotation", "ignoreConversionErrors"
    }:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки Edit Fields")
    if source_version is not None and isinstance(source_version, (int, float)) and not isinstance(source_version, bool):
        legacy_include = source_version < 3.3
    else:
        legacy_include = False
    # Явный параметр важнее версии: старые и новые workflow могут содержать
    # разные варианты настройки Include Other Input.
    if "includeOtherFields" in p:
        selection = p.get("include", "all") if include else "none"
    elif "include" in p:
        selection = p["include"]
        include = selection != "none"
    else:
        include = legacy_include
        selection = "all" if legacy_include else "none"
    selection_field = "includeFields" if selection == "selected" else "excludeFields" if selection == "except" else None
    fields_text = p.get(selection_field, "") if selection_field else ""
    if not isinstance(fields_text, str) or "{{" in fields_text or any(c in fields_text for c in ".["):
        raise WorkflowError(f"Узел {name}: поддерживается только статический список простых полей")
    selected_fields = [field.strip() for field in fields_text.split(",") if field.strip()]
    if selection == "selected":
        data_code = f'{{key: deepcopy(item["json"][key]) for key in {selected_fields!r} if key in item.get("json", {{}})}}'
    elif selection == "except":
        data_code = f'{{key: deepcopy(value) for key, value in item.get("json", {{}}).items() if key not in {selected_fields!r}}}'
    elif selection == "all":
        data_code = 'deepcopy(item.get("json", {}))'
    else:
        data_code = '{}'
    for option, value in options.items():
        if not isinstance(value, bool):
            raise WorkflowError(f"Узел {name}: настройка {option} должна быть логической")
    # При конфликте двух исторических вариантов версия помогает выбрать активный
    # ключ. Если задан только один известный ключ, читаем его в любой версии.
    if "stripBinary" in options and "includeBinary" in options:
        keep_binary = (
            include and selection != "none" and not options["stripBinary"]
            if version >= 3.4
            else options["includeBinary"]
        )
    elif "stripBinary" in options:
        keep_binary = include and selection != "none" and not options["stripBinary"]
    elif "includeBinary" in options:
        keep_binary = options["includeBinary"]
    else:
        keep_binary = (
            include and selection != "none" and not options.get("stripBinary", True)
            if version >= 3.4
            else options.get("includeBinary", True)
        )
    ignore_conversion_errors = options.get("ignoreConversionErrors", False)
    assignments = p.get("assignments", {})
    if not isinstance(assignments, dict) or set(assignments) - {"assignments"} or not isinstance(assignments.get("assignments", []), list):
        raise WorkflowError(f"Узел {name}: некорректные assignments")
    lines = []
    dependencies: set[str] = set()
    # Компилируем каждое назначение отдельно и переносим его в тело функции узла.
    for assignment in assignments.get("assignments", []):
        if not isinstance(assignment, dict) or set(assignment) - {"id", "name", "value", "type"}:
            raise WorkflowError(f"Узел {name}: некорректное назначение")
        field = assignment.get("name")
        value = assignment.get("value")
        if not isinstance(field, str) or not field or (options.get("dotNotation", True) and ("." in field or "[" in field)):
            raise WorkflowError(f"Узел {name}: неподдерживаемое имя поля {field!r}")
        field_type = assignment.get("type")
        if field_type not in {"string", "number", "boolean", "object", "array"}:
            raise WorkflowError(f"Узел {name}, поле {field}: неподдерживаемый тип {field_type!r}")
        if isinstance(value, str):
            expression = compile_expression(value, node=name, parameter=f"assignments.{field}")
            dependencies.update(expression.dependencies)
            code = expression.code
        else:
            code = repr(value)
        lines.extend([
            f"        value = convert_value({code}, {field_type!r}, ignore_errors={ignore_conversion_errors!r})",
            f"        data[{field!r}] = value",
        ])
    helper = '''def convert_value(value, field_type, ignore_errors=False):
    """Привести значение поля к типу, выбранному в Edit Fields.

    JSON-объекты и массивы разбираются из строки при необходимости; несовместимые
    значения вызывают ошибку, а не преобразуются молча.
    """
    if value is None:
        return None
    if field_type not in ("string", "boolean", "number", "object", "array"):
        raise ValueError("Неизвестный тип поля")
    try:
        if field_type == "string":
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (str, int)):
                return str(value)
            raise TypeError("Строковое значение не может быть корректно преобразовано")
        if field_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip() in ("0", "1"):
                return value.strip() == "1"
            raise ValueError("Значение не может быть преобразовано в boolean")
        if field_type == "number":
            if isinstance(value, (int, float)):
                return int(value) if isinstance(value, bool) else value
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return 0
                if re.fullmatch(r"[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
                    return float(text) if any(c in text for c in ".eE") else int(text)
            raise ValueError("Значение не может быть преобразовано в number")
        if field_type in ("object", "array"):
            if isinstance(value, str):
                value = json.loads(value)
            expected = dict if field_type == "object" else list
            if not isinstance(value, expected):
                raise TypeError("Некорректный тип JSON-значения")
            return deepcopy(value)
    except (TypeError, ValueError):
        if ignore_errors:
            return value
        raise
'''
    source = ('import json\nimport re\nfrom copy import deepcopy\n\n\n' + helper + '\n\n' + f'async def {function_name}(items, results, execution):\n' +
              '    """Применить Edit Fields к каждому входному item."""\n' +
              '    result = []\n    for item in items:\n' +
              f'        data = {data_code}\n' +
              ('\n'.join(lines) + '\n' if lines else '') +
              '        output = {"json": data}\n' +
              f'        if {keep_binary!r} and "binary" in item:\n            output["binary"] = dict(item["binary"])\n' +
              '        result.append(output)\n    return result\n')
    return source, dependencies
