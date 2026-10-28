"""Генерация маршрутизации IF и Switch для фильтров n8n."""

from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def _filter(condition: Any, node: str) -> tuple[str, set[str]]:
    """Скомпилировать фильтр n8n и собрать ссылки на предыдущие узлы."""
    if not isinstance(condition, dict) or set(condition) - {"options", "conditions", "combinator"}:
        raise WorkflowError(f"Узел {node}: некорректный фильтр")
    options = condition.get("options", {})
    if not isinstance(options, dict) or set(options) - {"caseSensitive", "leftValue", "typeValidation", "version"} or options.get("typeValidation", "strict") != "strict":
        raise WorkflowError(f"Узел {node}: неподдерживаемые настройки фильтра")
    case_sensitive = options.get("caseSensitive", True)
    if not isinstance(case_sensitive, bool):
        raise WorkflowError(f"Узел {node}: caseSensitive должен быть логическим")
    conditions = condition.get("conditions")
    combinator = condition.get("combinator", "and")
    if not isinstance(conditions, list) or not conditions or combinator not in ("and", "or"):
        raise WorkflowError(f"Узел {node}: необходимы условия и корректный combinator")
    compiled = []
    dependencies: set[str] = set()
    operators = {"string": {"equals", "notEquals", "contains", "notContains", "empty", "notEmpty"},
                 "number": {"equals", "notEquals", "gt", "gte", "lt", "lte"},
                 "boolean": {"true", "false", "equals", "notEquals"}}
    for entry in conditions:
        if not isinstance(entry, dict) or set(entry) - {"id", "leftValue", "rightValue", "operator"}:
            raise WorkflowError(f"Узел {node}: некорректное условие")
        operator = entry.get("operator", {})
        if not isinstance(operator, dict) or set(operator) - {"type", "operation", "singleValue"}:
            raise WorkflowError(f"Узел {node}: некорректный оператор")
        kind, operation = operator.get("type"), operator.get("operation")
        if kind not in operators or operation not in operators[kind]:
            raise WorkflowError(f"Узел {node}: неподдерживаемый оператор {kind}.{operation}")
        if "leftValue" not in entry or (operation not in {"true", "false", "empty", "notEmpty"} and "rightValue" not in entry):
            raise WorkflowError(f"Узел {node}: отсутствует значение условия")
        values = []
        for field in ("leftValue", "rightValue"):
            value = entry.get(field)
            if isinstance(value, str):
                expr = compile_expression(value, node=node, parameter=field)
                values.append(expr.code)
                dependencies.update(expr.dependencies)
            else:
                values.append(repr(value))
        compiled.append(f"compare({values[0]}, {values[1]}, {kind!r}, {operation!r}, {case_sensitive!r})")
    return (f" {combinator} ".join(f"({part})" for part in compiled), dependencies)


def generate_condition_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str], int]:
    """Создать функцию, возвращающую список items для каждого выхода."""
    name = node["name"]
    params = node["parameters"]
    kind = node["type"]
    dependencies: set[str] = set()
    if kind == "n8n-nodes-base.if":
        if set(params) - {"conditions", "options"} or params.get("options", {}):
            raise WorkflowError(f"Узел {name}: неподдерживаемые настройки IF")
        condition, dependencies = _filter(params.get("conditions"), name)
        count = 2
        body = f'        if {condition}:\n            outputs[0].append(item)\n        else:\n            outputs[1].append(item)\n'
    elif kind == "n8n-nodes-base.switch":
        if set(params) - {"mode", "rules", "options"} or params.get("mode", "rules") != "rules":
            raise WorkflowError(f"Узел {name}: поддерживается только Switch в режиме rules")
        rules = params.get("rules", {})
        options = params.get("options", {})
        if not isinstance(rules, dict) or set(rules) - {"values"} or not isinstance(rules.get("values"), list) or not rules["values"]:
            raise WorkflowError(f"Узел {name}: отсутствуют правила Switch")
        if not isinstance(options, dict) or set(options) - {"fallbackOutput", "allMatchingOutputs", "renameFallbackOutput"}:
            raise WorkflowError(f"Узел {name}: неподдерживаемые настройки Switch")
        fallback = options.get("fallbackOutput", "none")
        if fallback not in ("none", "extra") and (not isinstance(fallback, int) or isinstance(fallback, bool) or not 0 <= fallback < len(rules["values"])):
            raise WorkflowError(f"Узел {name}: неподдерживаемый fallback")
        all_matches = options.get("allMatchingOutputs", False)
        if not isinstance(all_matches, bool):
            raise WorkflowError(f"Узел {name}: allMatchingOutputs должен быть логическим")
        count = len(rules["values"]) + (fallback == "extra")
        body = '        matched = False\n'
        for index, rule in enumerate(rules["values"]):
            if not isinstance(rule, dict) or set(rule) - {"conditions", "renameOutput", "outputKey"}:
                raise WorkflowError(f"Узел {name}: неподдерживаемое правило Switch")
            expression, refs = _filter(rule.get("conditions"), name)
            dependencies.update(refs)
            body += f'        if {"True" if all_matches else "not matched"} and ({expression}):\n            outputs[{index}].append(item)\n            matched = True\n'
        if fallback == "extra":
            body += f'        if not matched:\n            outputs[{count - 1}].append(item)\n'
        elif isinstance(fallback, int):
            body += f'        if not matched:\n            outputs[{fallback}].append(item)\n'
    else:
        raise WorkflowError(f"Узел {name}: неподдерживаемый тип маршрутизации")
    helper = '''def compare(left, right, kind, operation, case_sensitive):
    """Сравнить значения по оператору и типу фильтра n8n."""
    expected = {"string": str, "number": (int, float), "boolean": bool}[kind]
    if not isinstance(left, expected) or (kind == "number" and isinstance(left, bool)):
        raise TypeError("Неверный тип левого значения условия")
    if operation not in {"true", "false", "empty", "notEmpty"}:
        if not isinstance(right, expected) or (kind == "number" and isinstance(right, bool)):
            raise TypeError("Неверный тип правого значения условия")
    if kind == "string" and not case_sensitive:
        left = left.lower()
        if isinstance(right, str):
            right = right.lower()
    if operation == "true": return left is True
    if operation == "false": return left is False
    if operation == "empty": return left == ""
    if operation == "notEmpty": return left != ""
    if operation == "equals": return left == right
    if operation == "notEquals": return left != right
    if operation == "contains": return right in left
    if operation == "notContains": return right not in left
    if operation == "gt": return left > right
    if operation == "gte": return left >= right
    if operation == "lt": return left < right
    if operation == "lte": return left <= right
    raise ValueError("Неизвестный оператор")
'''
    source = 'import json\n\n\n' + helper + f'\n\ndef {function_name}(items, results, execution):\n    """Разложить входные items по выходам IF или Switch."""\n    outputs = [[] for _ in range({count})]\n    for item in items:\n' + body + '    return outputs\n'
    return source, dependencies, count
