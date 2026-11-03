"""Генерация асинхронного узла Wait для самостоятельного Python-проекта.

Поддерживаются ожидание интервала и ожидание до заданной даты. Режимы webhook
и form требуют внешнего endpoint и сохранения приостановленного запуска, поэтому
генератор сообщает о них как о пропущенных частях workflow.
"""

import math
from datetime import datetime
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


_TIME_UNITS = {
    "seconds": 1,
    "minutes": 60,
    "hours": 60 * 60,
    "days": 24 * 60 * 60,
}


def _parameter_expression(value: Any, *, node: str, parameter: str) -> tuple[str, set[str]]:
    """Подготовить literal или expression и вернуть его зависимости."""
    if isinstance(value, str):
        expression = compile_expression(value, node=node, parameter=parameter)
        return expression.code, set(expression.dependencies)
    return repr(value), set()


def generate_wait_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Сгенерировать async-функцию Wait и собрать ссылки из выражений.

    Параметры интервала и даты компилируются на этапе конвертации. При запуске
    функция только вычисляет уже подготовленное значение, ждёт через
    ``asyncio.sleep`` и передаёт входные items дальше без изменения.

    Режимы ``webhook`` и ``form`` нельзя достоверно выполнить в обычном
    standalone-проекте: они требуют HTTP-сервера и сохранённого состояния,
    которое продолжает выполняться по внешнему запросу.
    """
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    if not isinstance(parameters, dict):
        raise WorkflowError(f"Узел {name}: параметры Wait должны быть объектом")

    resume = parameters.get("resume", "timeInterval")
    if not isinstance(resume, str):
        raise WorkflowError(f"Узел Wait {name}: параметр resume должен быть строкой")
    if resume in {"webhook", "form"}:
        mode_name = "webhook" if resume == "webhook" else "формы"
        raise WorkflowError(
            f"Узел Wait {name}: режим возобновления {mode_name} требует HTTP-сервера "
            "и сохранения приостановленного запуска; эта часть workflow пропущена. "
            "Режимы timeInterval и specificTime поддерживаются."
        )
    if resume not in {"timeInterval", "specificTime"}:
        raise WorkflowError(f"Узел Wait {name}: неподдерживаемый режим resume={resume!r}")

    known_parameters = {
        "resume",
        "amount",
        "unit",
        "dateTime",
        "limitWaitTime",
        "limitType",
        "resumeAmount",
        "resumeUnit",
        "maxDateAndTime",
        "incomingAuthentication",
        "httpMethod",
        "responseCode",
        "responseData",
        "responseMode",
        "responseBinaryPropertyName",
        "webhookSuffix",
        "binaryPropertyName",
        "options",
    }
    unknown = sorted(set(parameters) - known_parameters)
    if unknown:
        raise WorkflowError(
            f"Узел Wait {name}: неподдерживаемые параметры: {', '.join(unknown)}"
        )

    options = parameters.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел Wait {name}: параметр options должен быть объектом")
    if options:
        raise WorkflowError(
            f"Узел Wait {name}: настройки options применимы к webhook/form и не поддерживаются"
        )

    original_version = node.get("_n8nOriginalTypeVersion", node.get("typeVersion", 1.1))
    old_defaults = isinstance(original_version, (int, float)) and original_version <= 1
    dependencies: set[str] = set()

    if resume == "timeInterval":
        default_amount = 1 if old_defaults else 5
        default_unit = "hours" if old_defaults else "seconds"
        amount = parameters.get("amount", default_amount)
        if isinstance(amount, str) and "{{" not in amount and not amount.startswith("="):
            try:
                numeric_amount = float(amount)
            except ValueError as exc:
                raise WorkflowError(
                    f"Узел Wait {name}: amount должен быть числом или поддерживаемым expression"
                ) from exc
            if not math.isfinite(numeric_amount) or numeric_amount < 0:
                raise WorkflowError(f"Узел Wait {name}: amount должен быть конечным числом не меньше 0")
            amount = int(numeric_amount) if numeric_amount.is_integer() else numeric_amount
        elif isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
            raise WorkflowError(
                f"Узел Wait {name}: amount должен быть числом или поддерживаемым expression"
            )
        elif isinstance(amount, (int, float)) and (
            not math.isfinite(float(amount)) or amount < 0
        ):
            raise WorkflowError(f"Узел Wait {name}: amount должен быть конечным числом не меньше 0")
        amount_code, amount_dependencies = _parameter_expression(
            amount,
            node=name,
            parameter="amount",
        )
        dependencies.update(amount_dependencies)
        unit = parameters.get("unit", default_unit)
        if not isinstance(unit, str) or unit not in _TIME_UNITS:
            raise WorkflowError(
                f"Узел Wait {name}: unit должен быть одним из seconds, minutes, hours или days"
            )
        wait_code = (
            f"    amount = {amount_code}\n"
            "    if isinstance(amount, bool) or not isinstance(amount, (int, float)):\n"
            '        raise TypeError("Wait amount должен быть числом")\n'
            "    if not math.isfinite(float(amount)) or amount < 0:\n"
            '        raise ValueError("Wait amount должен быть конечным числом не меньше 0")\n'
            f"    await asyncio.sleep(float(amount) * {_TIME_UNITS[unit]})\n"
        )
    else:
        date_time = parameters.get("dateTime")
        if not isinstance(date_time, str) or not date_time.strip():
            raise WorkflowError(f"Узел Wait {name}: для resume='specificTime' нужен dateTime")
        if "{{" not in date_time and not date_time.startswith("="):
            try:
                datetime.fromisoformat(date_time.replace("Z", "+00:00"))
            except ValueError as exc:
                raise WorkflowError(
                    f"Узел Wait {name}: dateTime должен быть датой ISO 8601 или expression"
                ) from exc
        date_code, date_dependencies = _parameter_expression(
            date_time,
            node=name,
            parameter="dateTime",
        )
        dependencies.update(date_dependencies)
        wait_code = (
            f"    date_time = {date_code}\n"
            "    if not isinstance(date_time, str) or not date_time.strip():\n"
            '        raise TypeError("Wait dateTime должен быть непустой строкой")\n'
            '    target = datetime.fromisoformat(date_time.replace("Z", "+00:00"))\n'
            "    # Для даты без часового пояса используется локальное время процесса,\n"
            "    # как локальное серверное время n8n.\n"
            "    now = datetime.now(target.tzinfo) if target.tzinfo else datetime.now()\n"
            "    delay = max((target - now).total_seconds(), 0)\n"
            "    await asyncio.sleep(delay)\n"
        )

    source = (
        '"""Асинхронное ожидание, преобразованное из узла n8n Wait."""\n\n'
        "import asyncio\n"
        "import math\n"
        "from datetime import datetime\n\n\n"
        f"async def {function_name}(items, results, execution):\n"
        '    """Дождаться заданного времени и вернуть входные items без изменений."""\n'
        '    item = items[0] if items else {"json": {}}\n'
        + wait_code
        + "    return items\n"
    )
    return source, dependencies
