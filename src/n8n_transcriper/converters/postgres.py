"""Генерация ограниченного безопасного SQL для PostgreSQL."""

import re
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


POSTGRES_MODULE = '''import psycopg
from psycopg.rows import dict_row
import config


def connect(timeout):
    return psycopg.connect(
        host=config.POSTGRES_HOST, port=config.POSTGRES_PORT,
        dbname=config.POSTGRES_DATABASE, user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD, connect_timeout=timeout, row_factory=dict_row,
    )
'''


def generate_postgres_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Поддержать независимый SELECT с безопасной привязкой параметров."""
    name = node.get("name", "<без имени>")
    p = node.get("parameters", {})
    if set(p) - {"resource", "operation", "query", "options"} or p.get("resource", "database") != "database" or p.get("operation") != "executeQuery":
        raise WorkflowError(f"Узел {name}: поддерживается только PostgreSQL executeQuery")
    version = node.get("typeVersion")
    if not isinstance(version, (float, int)) or isinstance(version, bool) or version < 2.8:
        raise WorkflowError(f"Узел {name}: требуется PostgreSQL версии 2.8 или выше")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"queryBatching", "queryReplacement", "connectionTimeout", "largeNumbersOutput"} or options.get("queryBatching") != "independently":
        raise WorkflowError(f"Узел {name}: требуется независимое выполнение запросов")
    query = p.get("query")
    if not isinstance(query, str) or not re.match(r"\s*SELECT\b", query, re.I) or not re.fullmatch(r"[a-zA-Z0-9_\s.,()*=<>!+$-]+;?\s*", query) or "--" in query:
        raise WorkflowError(f"Узел {name}: поддерживается простой SELECT без выражений, комментариев и литералов")
    tokens = re.findall(r"\$(\d+)", query)
    if any(int(token) < 1 or token.startswith("0") for token in tokens) or "$" in re.sub(r"\$\d+", "", query):
        raise WorkflowError(f"Узел {name}: некорректный параметр SQL")
    replacement = options.get("queryReplacement", "")
    if tokens:
        if not isinstance(replacement, str) or not re.fullmatch(r"=\s*{{\s*[^{}]+\s*}}", replacement):
            raise WorkflowError(f"Узел {name}: для параметров SQL требуется одно выражение queryReplacement")
        expression = compile_expression(replacement, node=name, parameter="options.queryReplacement")
        value = expression.code
        dependencies = set(expression.dependencies)
        query = re.sub(r"\$(\d+)", lambda match: f'%(p{match.group(1)})s', query)
        arguments = 'parameters'
    else:
        if replacement:
            raise WorkflowError(f"Узел {name}: queryReplacement задан без $1")
        arguments = "None"
        dependencies = set()
    timeout = options.get("connectionTimeout", 30)
    number_format = options.get("largeNumbersOutput", "text")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1 or number_format != "text":
        raise WorkflowError(f"Узел {name}: некорректные настройки подключения или чисел")
    parameter_code = (f'        parameter_values = {value}\n'
                      '        if not isinstance(parameter_values, list):\n            parameter_values = [parameter_values]\n'
                      f'        if len(parameter_values) < {max(map(int, tokens))}:\n            raise ValueError("Недостаточно параметров PostgreSQL")\n'
                      '        parameters = {}\n'
                      '        for index, parameter in enumerate(parameter_values, 1):\n'
                      '            if isinstance(parameter, (dict, list)):\n                parameter = json.dumps(parameter, ensure_ascii=False, separators=(",", ":"))\n'
                      '            elif parameter is not None and not isinstance(parameter, (str, int, float, bool)):\n'
                      '                raise TypeError("Неподдерживаемый параметр PostgreSQL")\n'
                      '            parameters[f"p{index}"] = parameter\n') if tokens else ''
    source = f'''import json
from database.postgres import connect


def {function_name}(items, results, execution):
    output = []
    for item in items:
{parameter_code}
        with connect({timeout!r}) as conn:
            with conn.cursor() as cursor:
                cursor.execute({query!r}, {arguments})
                for row in cursor.fetchall():
                    for column in cursor.description:
                        if column.type_code in (20, 1700) and row[column.name] is not None:
                            row[column.name] = str(row[column.name])
                    output.append({{"json": row}})
    return output
'''
    return source, dependencies
