"""Генерация CRUD-узлов Data Table через асинхронный PostgreSQL-клиент.

Каждая сгенерированная функция содержит SQL конкретной операции. Общий модуль
generated-проекта отвечает за соединение, безопасные имена, фильтры и упаковку
строк в n8n items.
"""

from __future__ import annotations

from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


DATA_TABLES_MODULE = '''"""Асинхронное подключение и общие helpers для Data Table узлов."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
import config


async def connect():
    """Открыть асинхронное PostgreSQL-соединение из config.py."""
    required = {
        "POSTGRES_HOST": config.POSTGRES_HOST,
        "POSTGRES_DATABASE": config.POSTGRES_DATABASE,
        "POSTGRES_USER": config.POSTGRES_USER,
        "POSTGRES_PASSWORD": config.POSTGRES_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Заполните параметры PostgreSQL в config.py или переменных окружения: "
            + ", ".join(missing)
        )
    return await asyncpg.connect(
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        database=config.POSTGRES_DATABASE,
        user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD,
        timeout=config.POSTGRES_CONNECT_TIMEOUT,
    )


def quote_identifier(identifier):
    """Экранировать имя схемы, таблицы или столбца как SQL identifier."""
    if not isinstance(identifier, str) or not identifier or "\\x00" in identifier:
        raise ValueError("Имя таблицы или столбца PostgreSQL должно быть непустой строкой")
    return '"' + identifier.replace('"', '""') + '"'


def quote_table(table_reference):
    """Преобразовать ссылку n8n на таблицу в безопасное имя PostgreSQL."""
    if not isinstance(table_reference, str) or not table_reference:
        raise ValueError("Data Table должен содержать непустую ссылку на таблицу")
    target = config.DATA_TABLES.get(table_reference, table_reference)
    if not isinstance(target, str) or not target:
        raise ValueError(f"Для Data Table {table_reference!r} не задано имя PostgreSQL-таблицы")
    parts = target.split(".")
    if len(parts) > 2:
        raise ValueError("Укажите PostgreSQL-таблицу как table или schema.table")
    return ".".join(quote_identifier(part) for part in parts)


def validate_row(row):
    """Проверить значения записи перед параметризованной SQL-операцией."""
    if not isinstance(row, dict):
        raise TypeError("Значения Data Table должны образовывать объект")
    for column, value in row.items():
        quote_identifier(column)
        if isinstance(value, (dict, list, tuple, set)):
            raise TypeError(
                f"Столбец {column!r}: Data Table поддерживает скалярные значения, "
                "а не массивы или объекты"
            )
        if value is not None and not isinstance(
            value, (str, int, float, bool, date, datetime, Decimal, UUID)
        ):
            raise TypeError(f"Столбец {column!r}: неподдерживаемый тип значения {type(value).__name__}")
    return row


def _json_value(value):
    """Преобразовать значения asyncpg в простые JSON-совместимые типы."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def wrap_rows(rows, input_index=None):
    """Обернуть каждую строку БД в отдельный n8n item с полем json."""
    output = []
    for row in rows:
        item = {"json": _json_value(dict(row))}
        if input_index is not None:
            item["pairedItem"] = {"item": input_index}
        output.append(item)
    return output


def build_where(conditions, match_type, parameter_offset=0):
    """Собрать безопасный WHERE из условий Data Table."""
    if not conditions:
        return "", []
    joiner = {
        "allConditions": " AND ",
        "anyCondition": " OR ",
        "all": " AND ",
        "any": " OR ",
    }.get(match_type)
    if joiner is None:
        raise ValueError(f"Неподдерживаемый режим объединения условий: {match_type!r}")
    clauses = []
    values = []
    operators = {
        "eq": "=",
        "neq": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "like": "LIKE",
        "ilike": "ILIKE",
    }
    for column, condition, value in conditions:
        quoted_column = quote_identifier(column)
        if condition == "isEmpty":
            clauses.append(f"({quoted_column} IS NULL OR {quoted_column}::text = '')")
        elif condition == "isNotEmpty":
            clauses.append(f"({quoted_column} IS NOT NULL AND {quoted_column}::text <> '')")
        elif condition == "isTrue":
            clauses.append(f"{quoted_column} IS TRUE")
        elif condition == "isFalse":
            clauses.append(f"{quoted_column} IS FALSE")
        elif condition in {"eq", "neq"} and value is None:
            clauses.append(f"{quoted_column} IS {'NOT ' if condition == 'neq' else ''}NULL")
        elif condition in operators:
            values.append(value)
            parameter = parameter_offset + len(values)
            clauses.append(f"{quoted_column} {operators[condition]} $" + str(parameter))
        else:
            raise ValueError(f"Неподдерживаемое условие Data Table: {condition!r}")
    return " WHERE " + joiner.join(clauses), values
'''


_OPERATORS_WITH_VALUE = {"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike"}
_OPERATORS_WITHOUT_VALUE = {"isEmpty", "isNotEmpty", "isTrue", "isFalse"}
_SUPPORTED_OPERATORS = _OPERATORS_WITH_VALUE | _OPERATORS_WITHOUT_VALUE
_SYSTEM_COLUMNS = {"id", "createdAt", "updatedAt"}
_SUPPORTED_OPERATIONS = {"insert", "get", "update", "upsert", "deleteRows"}


def _is_expression(value: str) -> bool:
    """Определить, содержит ли строковый параметр выражение n8n."""
    return value.lstrip().startswith("=") or "{{" in value


def _value_code(
    value: Any,
    *,
    node_name: str,
    parameter: str,
    dependencies: set[str],
) -> str:
    """Скомпилировать значение expression или вернуть его Python-литералом."""
    if isinstance(value, str):
        if _is_expression(value):
            expression = compile_expression(value, node=node_name, parameter=parameter)
            dependencies.update(expression.dependencies)
            return expression.code
        return repr(value)
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    raise WorkflowError(
        f"Узел {node_name}, параметр {parameter}: Data Table принимает только скалярные значения"
    )


def _row_lines(
    parameters: dict[str, Any],
    *,
    node_name: str,
    dependencies: set[str],
) -> list[str]:
    """Сгенерировать код формирования записи из input item."""
    columns = parameters.get("columns", {})
    if not isinstance(columns, dict):
        raise WorkflowError(f"Узел {node_name}: параметр columns должен быть объектом")
    mapping_mode = columns.get("mappingMode", "defineBelow")
    if mapping_mode == "autoMapInputData":
        return [
            "    row = dict(item.get('json', {}))",
            "    for system_column in ('id', 'createdAt', 'updatedAt'):",
            "        row.pop(system_column, None)",
        ]
    if mapping_mode != "defineBelow":
        raise WorkflowError(
            f"Узел {node_name}: неподдерживаемый режим сопоставления columns.mappingMode={mapping_mode!r}"
        )
    values = columns.get("value", {})
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise WorkflowError(f"Узел {node_name}: columns.value должен быть объектом")
    lines = ["    row = {"]
    for key, value in values.items():
        if not isinstance(key, str) or key in _SYSTEM_COLUMNS:
            raise WorkflowError(
                f"Узел {node_name}: нельзя задавать системный или некорректный столбец {key!r}"
            )
        code = _value_code(
            value,
            node_name=node_name,
            parameter=f"columns.value.{key}",
            dependencies=dependencies,
        )
        lines.append(f"        {key!r}: {code},")
    lines.append("    }")
    return lines


def _condition_lines(
    parameters: dict[str, Any],
    *,
    node_name: str,
    dependencies: set[str],
) -> tuple[list[str], int]:
    """Скомпилировать фильтры для текущего входного item."""
    filters = parameters.get("filters", {})
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise WorkflowError(f"Узел {node_name}: параметр filters должен быть объектом")
    conditions = filters.get("conditions", [])
    if conditions is None:
        conditions = []
    if not isinstance(conditions, list):
        raise WorkflowError(f"Узел {node_name}: filters.conditions должен быть списком")
    lines = ["    conditions = ["]
    for index, condition in enumerate(conditions):
        path = f"filters.conditions[{index}]"
        if not isinstance(condition, dict):
            raise WorkflowError(f"Узел {node_name}, параметр {path}: условие должно быть объектом")
        column = condition.get("keyName")
        operator = condition.get("condition", "eq")
        if not isinstance(column, str) or not column:
            raise WorkflowError(f"Узел {node_name}, параметр {path}.keyName: задайте столбец")
        if not isinstance(operator, str) or operator not in _SUPPORTED_OPERATORS:
            raise WorkflowError(
                f"Узел {node_name}, параметр {path}.condition: неподдерживаемое условие {operator!r}"
            )
        column_code = _value_code(
            column,
            node_name=node_name,
            parameter=f"{path}.keyName",
            dependencies=dependencies,
        )
        if operator in _OPERATORS_WITH_VALUE:
            if "keyValue" not in condition:
                raise WorkflowError(f"Узел {node_name}, параметр {path}: отсутствует keyValue")
            value_code = _value_code(
                condition["keyValue"],
                node_name=node_name,
                parameter=f"{path}.keyValue",
                dependencies=dependencies,
            )
        else:
            value_code = "None"
        lines.append(f"        ({column_code}, {operator!r}, {value_code}),")
    lines.append("    ]")
    return lines, len(conditions)


def _table_reference(
    parameters: dict[str, Any],
    *,
    node_name: str,
    dependencies: set[str],
) -> tuple[str, tuple[str, str] | None]:
    """Скомпилировать resourceLocator и вернуть статическую ссылку для config."""
    locator = parameters.get("dataTableId")
    if isinstance(locator, dict):
        mode = locator.get("mode", "list")
        value = locator.get("value")
    else:
        mode, value = "list", locator
    if not isinstance(mode, str) or mode not in {"list", "id", "name"}:
        raise WorkflowError(f"Узел {node_name}: неподдерживаемый режим dataTableId.mode={mode!r}")
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"Узел {node_name}: не задана ссылка dataTableId.value")
    if _is_expression(value):
        code = _value_code(
            value,
            node_name=node_name,
            parameter="dataTableId.value",
            dependencies=dependencies,
        )
        return code, None
    return repr(value), (mode, value)


def generate_data_table_node(
    node: dict[str, Any],
    function_name: str,
) -> tuple[str, set[str], tuple[str, str] | None]:
    """Сгенерировать asyncpg CRUD-функцию и её expression-зависимости.

    Поддерживаются строковые операции insert, get, update, upsert и deleteRows.
    Значения передаются драйверу отдельными параметрами, а строки БД
    оборачиваются в формат items узла n8n.
    """
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    if not isinstance(parameters, dict):
        raise WorkflowError(f"Узел {name}: parameters должен быть объектом")
    if parameters.get("resource", "row") != "row":
        raise WorkflowError(f"Узел {name}: поддерживается ресурс Data Table row")
    operation = parameters.get("operation")
    if operation == "delete":
        operation = "deleteRows"
    if not isinstance(operation, str) or operation not in _SUPPORTED_OPERATIONS:
        raise WorkflowError(
            f"Узел {name}: поддерживаются только операции Data Table insert, get, update, upsert и delete"
        )
    allowed = {"resource", "operation", "dataTableId"}
    if operation in {"insert", "update", "upsert"}:
        allowed.add("columns")
    if operation in {"get", "update", "upsert", "deleteRows"}:
        allowed.update({"matchType", "filters"})
    if operation == "get":
        allowed.update({"returnAll", "limit", "orderBy", "orderByColumn", "orderByDirection"})
    if operation in {"insert", "update", "upsert", "deleteRows"}:
        allowed.add("options")
    extra = set(parameters) - allowed
    if extra:
        raise WorkflowError(
            f"Узел {name}: неподдерживаемые параметры Data Table: {', '.join(sorted(extra))}"
        )
    options = parameters.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: параметр options должен быть объектом")
    if operation == "insert":
        unsupported_options = set(options) - {"optimizeBulk"}
        if unsupported_options:
            raise WorkflowError(
                f"Узел {name}: неподдерживаемые параметры insert.options: "
                f"{', '.join(sorted(unsupported_options))}"
            )
    elif operation in {"update", "upsert", "deleteRows"}:
        unsupported_options = set(options) - {"dryRun"}
        if unsupported_options:
            raise WorkflowError(
                f"Узел {name}: неподдерживаемые параметры options: {', '.join(sorted(unsupported_options))}"
            )
        if options.get("dryRun", False):
            raise WorkflowError(f"Узел {name}: options.dryRun пока не поддерживается")
    if operation == "get" and options:
        raise WorkflowError(f"Узел {name}: у операции get не поддерживаются дополнительные options")

    dependencies: set[str] = set()
    table_code, table_reference = _table_reference(
        parameters, node_name=name, dependencies=dependencies
    )
    row_code = []
    if operation in {"insert", "update", "upsert"}:
        row_code = _row_lines(parameters, node_name=name, dependencies=dependencies)
    condition_code = []
    condition_count = 0
    if operation in {"get", "update", "upsert", "deleteRows"}:
        condition_code, condition_count = _condition_lines(
            parameters, node_name=name, dependencies=dependencies
        )
    if operation in {"update", "upsert", "deleteRows"} and condition_count == 0:
        raise WorkflowError(f"Узел {name}: операция {operation} требует хотя бы одно условие filters")

    match_type = parameters.get("matchType", "anyCondition")
    if not isinstance(match_type, str) or match_type not in {"anyCondition", "allConditions", "any", "all"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемое значение matchType={match_type!r}")

    source = [
        '"""Асинхронная реализация Data Table узла из workflow n8n."""',
        "",
        "from database.data_tables import build_where, connect, quote_identifier, quote_table, validate_row, wrap_rows",
        "",
    ]
    if row_code:
        source.extend(
            [
                "",
                "def _row_for_item(item, results, execution):",
                '    """Собрать запись из input item согласно mappingMode узла."""',
                *row_code,
                "    return validate_row(row)",
                "",
            ]
        )
    if condition_code:
        source.extend(
            [
                "",
                "def _conditions_for_item(item, items, results, execution):",
                '    """Вычислить условия выборки для текущего input item."""',
                *condition_code,
                "    return conditions",
                "",
            ]
        )
    source.extend(
        [
            "",
            f"async def {function_name}(items, results, execution):",
            '    """Выполнить операцию Data Table и вернуть n8n items."""',
            "    output = []",
            "    if not items:",
            "        return output",
            "    conn = await connect()",
            "    try:",
            "        for item_index, item in enumerate(items):",
            f"            table = quote_table({table_code})",
        ]
    )

    if operation == "insert":
        source.extend(
            [
                "            row = _row_for_item(item, results, execution)",
                "            columns = list(row)",
                "            if columns:",
                '                quoted_columns = ", ".join([quote_identifier(column) for column in columns] + [quote_identifier("createdAt"), quote_identifier("updatedAt")])',
                '                placeholders = ", ".join("$" + str(index) for index in range(1, len(columns) + 1))',
                '                query = f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}, now(), now()) RETURNING *"',
                "                rows = await conn.fetch(query, *(row[column] for column in columns))",
                "            else:",
                '                rows = await conn.fetch(f"INSERT INTO {table} (\\\"createdAt\\\", \\\"updatedAt\\\") VALUES (now(), now()) RETURNING *")',
                "            output.extend(wrap_rows(rows, item_index))",
            ]
        )
        if options.get("optimizeBulk", False):
            source.append('        return [{"json": {"success": True, "insertedRows": len(output)}}]')
    elif operation == "get":
        return_all = parameters.get("returnAll", False)
        if not isinstance(return_all, bool):
            raise WorkflowError(f"Узел {name}: returnAll должен быть логическим значением")
        limit = parameters.get("limit", 50)
        if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
            limit_code = repr(limit)
        elif isinstance(limit, str) and _is_expression(limit):
            limit_code = _value_code(
                limit,
                node_name=name,
                parameter="limit",
                dependencies=dependencies,
            )
        else:
            raise WorkflowError(f"Узел {name}: limit должен быть целым числом не меньше 1")
        order_by = parameters.get("orderBy", False)
        if not isinstance(order_by, bool):
            raise WorkflowError(f"Узел {name}: orderBy должен быть логическим значением")
        order_column = parameters.get("orderByColumn", "createdAt")
        order_direction = parameters.get("orderByDirection", "DESC")
        if not isinstance(order_direction, str) or order_direction not in {"ASC", "DESC"}:
            raise WorkflowError(f"Узел {name}: orderByDirection должен быть ASC или DESC")
        order_code = _value_code(
            order_column,
            node_name=name,
            parameter="orderByColumn",
            dependencies=dependencies,
        )
        source.extend(
            [
                "            conditions = _conditions_for_item(item, items, results, execution)",
                f"            where_sql, values = build_where(conditions, {match_type!r})",
            ]
        )
        if order_by:
            source.extend(
                [
                    f"            order_column = {order_code}",
                    f'            order_sql = f" ORDER BY {{quote_identifier(order_column)}} {order_direction}"',
                ]
            )
        else:
            source.append('            order_sql = ""')
        if return_all:
            source.append('            query = f"SELECT * FROM {table}{where_sql}{order_sql}"')
            source.append("            rows = await conn.fetch(query, *values)")
        else:
            source.append(f"            limit_value = {limit_code}")
            source.append(
                '            if not isinstance(limit_value, int) or isinstance(limit_value, bool) or limit_value < 1:'
            )
            source.append('                raise ValueError("Параметр limit узла Data Table должен быть целым числом не меньше 1")')
            source.append("            values.append(limit_value)")
            source.append('            query = f"SELECT * FROM {table}{where_sql}{order_sql} LIMIT $" + str(len(values))')
            source.append("            rows = await conn.fetch(query, *values)")
        source.append("            output.extend(wrap_rows(rows, item_index))")
    elif operation == "deleteRows":
        source.extend(
            [
                "            conditions = _conditions_for_item(item, items, results, execution)",
                f"            where_sql, values = build_where(conditions, {match_type!r})",
                '            rows = await conn.fetch(f"DELETE FROM {table}{where_sql} RETURNING *", *values)',
                "            output.extend(wrap_rows(rows, item_index))",
            ]
        )
    elif operation == "update":
        source.extend(
            [
                "            row = _row_for_item(item, results, execution)",
                "            conditions = _conditions_for_item(item, items, results, execution)",
                f"            where_sql, filter_values = build_where(conditions, {match_type!r}, parameter_offset=len(row))",
                "            if not row:",
                '                raise ValueError("Для Data Table update задайте хотя бы один обновляемый столбец")',
                "            columns = list(row)",
                '            assignments = ", ".join([quote_identifier(column) + " = $" + str(index) for index, column in enumerate(columns, 1)] + [quote_identifier("updatedAt") + " = now()"])',
                '            query = f"UPDATE {table} SET {assignments}{where_sql} RETURNING *"',
                "            rows = await conn.fetch(query, *(row[column] for column in columns), *filter_values)",
                "            output.extend(wrap_rows(rows, item_index))",
            ]
        )
    else:  # upsert
        source.extend(
            [
                "            row = _row_for_item(item, results, execution)",
                "            conditions = _conditions_for_item(item, items, results, execution)",
                f"            where_sql, filter_values = build_where(conditions, {match_type!r})",
                f"            update_where_sql, _ = build_where(conditions, {match_type!r}, parameter_offset=len(row))",
                "            if not row:",
                '                raise ValueError("Для Data Table upsert задайте хотя бы один записываемый столбец")',
                "            columns = list(row)",
                "            async with conn.transaction():",
                "                # Блокировка не даёт параллельным upsert-вызовам вставить дубликаты.",
                '                await conn.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")',
                '                existing = await conn.fetch(f"SELECT 1 FROM {table}{where_sql} LIMIT 1 FOR UPDATE", *filter_values)',
                "                if existing:",
                '                    assignments = ", ".join([quote_identifier(column) + " = $" + str(index) for index, column in enumerate(columns, 1)] + [quote_identifier("updatedAt") + " = now()"])',
                '                    query = f"UPDATE {table} SET {assignments}{update_where_sql} RETURNING *"',
                "                    rows = await conn.fetch(query, *(row[column] for column in columns), *filter_values)",
                "                else:",
                '                    quoted_columns = ", ".join([quote_identifier(column) for column in columns] + [quote_identifier("createdAt"), quote_identifier("updatedAt")])',
                '                    placeholders = ", ".join("$" + str(index) for index in range(1, len(columns) + 1))',
                '                    query = f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}, now(), now()) RETURNING *"',
                "                    rows = await conn.fetch(query, *(row[column] for column in columns))",
                "            output.extend(wrap_rows(rows, item_index))",
            ]
        )
    source.extend(
        [
            "    finally:",
            "        await conn.close()",
            "    return output",
            "",
        ]
    )
    return "\n".join(source), dependencies, table_reference
