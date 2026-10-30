"""Генерация параметризованных запросов PostgreSQL.

Модуль принимает ограниченный набор простых SELECT/INSERT/UPDATE/DELETE,
переводит $1-плейсхолдеры в именованные параметры psycopg и задаёт границы
транзакций согласно настройкам узла n8n.
"""

import re
import textwrap
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


POSTGRES_MODULE = '''"""Общие настройки подключения к PostgreSQL generated-проекта."""

import psycopg
from psycopg.rows import dict_row
import config


def connect(timeout):
    """Открыть соединение psycopg с credentials из config.py.

    Словарный row_factory возвращает результаты в форме, удобной для JSON items.
    Владелец соединения отвечает за его закрытие и commit/rollback.
    """
    return psycopg.connect(
        host=config.POSTGRES_HOST, port=config.POSTGRES_PORT,
        dbname=config.POSTGRES_DATABASE, user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD, connect_timeout=timeout, row_factory=dict_row,
    )
'''


def generate_postgres_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Сгенерировать SQL-функцию и зависимости параметров от предыдущих узлов.

    Текст запроса проверяется до генерации, а значения из queryReplacement
    передаются драйверу отдельно от SQL. Batching задаёт, выполняются ли запросы
    по одному или используют общую транзакцию.

    Returns:
        Исходный текст модуля и множество имён узлов из expressions параметров.
    """
    name = node.get("name", "<без имени>")
    p = node.get("parameters", {})
    if set(p) - {"resource", "operation", "query", "options"} or p.get("resource", "database") != "database" or p.get("operation") != "executeQuery":
        raise WorkflowError(f"Узел {name}: поддерживается только PostgreSQL executeQuery")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"queryBatching", "queryReplacement", "connectionTimeout", "largeNumbersOutput"} or options.get("queryBatching") not in {"independently", "transaction"}:
        raise WorkflowError(f"Узел {name}: требуется независимое выполнение запросов или transaction")
    # Разрешён только простой SQL без комментариев, строковых литералов и динамического SQL.
    query = p.get("query")
    if not isinstance(query, str) or not re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE)\b", query, re.I) or not re.fullmatch(r"[a-zA-Z0-9_\s.,()*=<>!+$-]+;?\s*", query) or "--" in query:
        raise WorkflowError(f"Узел {name}: поддерживается простой SELECT, INSERT, UPDATE или DELETE без выражений, комментариев и литералов")
    is_select = bool(re.match(r"\s*SELECT\b", query, re.I))
    tokens = re.findall(r"\$(\d+)", query)
    if any(int(token) < 1 or token.startswith("0") for token in tokens) or "$" in re.sub(r"\$\d+", "", query):
        raise WorkflowError(f"Узел {name}: некорректный параметр SQL")
    # Каждому $N сопоставляется отдельное значение из queryReplacement.
    replacement = options.get("queryReplacement", "")
    if tokens:
        if not isinstance(replacement, str):
            raise WorkflowError(f"Узел {name}: queryReplacement должен содержать выражения")
        parts = re.split(r"\s*,\s*(?==?\s*{{)", replacement)
        if not parts or any(not re.fullmatch(r"=?\s*{{\s*[^{}]+\s*}}", part) for part in parts):
            raise WorkflowError(f"Узел {name}: queryReplacement должен содержать выражения через запятую")
        expressions = [compile_expression(part.strip(), node=name, parameter="options.queryReplacement") for part in parts]
        value = "[" + ", ".join(expr.code for expr in expressions) + "]"
        dependencies = set().union(*(expr.dependencies for expr in expressions))
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
    parameter_code = (f'        parameter_groups = {value}\n'
                      '        parameter_values = []\n'
                      '        for group in parameter_groups:\n'
                      '            parameter_values.extend(group if isinstance(group, list) else [group])\n'
                      f'        if len(parameter_values) < {max(map(int, tokens))}:\n            raise ValueError("Недостаточно параметров PostgreSQL")\n'
                      '        parameters = {}\n'
                      '        for index, parameter in enumerate(parameter_values, 1):\n'
                      '            if isinstance(parameter, (dict, list)):\n                parameter = json.dumps(parameter, ensure_ascii=False, separators=(",", ":"))\n'
                      '            elif parameter is not None and not isinstance(parameter, (str, int, float, bool)):\n'
                      '                raise TypeError("Неподдерживаемый параметр PostgreSQL")\n'
                      '            parameters[f"p{index}"] = parameter\n') if tokens else ''
    parameter_code = textwrap.dedent(parameter_code)
    parameter_code = textwrap.indent(parameter_code, "    ") if parameter_code else ""
    # В transaction все items используют одно соединение и общую транзакцию.
    if options["queryBatching"] == "transaction":
        run_body = f'''    with connect({timeout!r}) as conn:
        for item in items:
            output.extend(execute_one(conn, item, items, results, execution))
'''
    else:
        run_body = f'''    for item in items:
        with connect({timeout!r}) as conn:
            output.extend(execute_one(conn, item, items, results, execution))
'''
    source = f'''import json
from database.postgres import connect


def execute_one(conn, item, items, results, execution):
    """Подготовить SQL-параметры, выполнить один запрос и обернуть строки в items."""
{parameter_code}    with conn.cursor() as cursor:
        cursor.execute({query!r}, {arguments})
        rows = cursor.fetchall() if cursor.description is not None else []
        # Decimal и большие целые превращаются в строки для устойчивой JSON-сериализации.
        for row in rows:
            for column in cursor.description:
                if column.type_code in (20, 1700) and row[column.name] is not None:
                    row[column.name] = str(row[column.name])
        if not rows and not {is_select!r}:
            rows = [{{"success": True}}]
        return [{{"json": row}} for row in rows]


def {function_name}(items, results, execution):
    """Выполнить запрос для входных items с заданным режимом batching."""
    output = []
{run_body}    return output
'''
    return source, dependencies
