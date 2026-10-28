"""Генерация HTTP-функций для поддерживаемых настроек n8n."""

import keyword
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def generate_http_node(node: dict[str, Any], function_name: str) -> str:
    """Создать модуль HTTP-запроса; неизвестные настройки отклоняются."""
    name = node.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    p = node.get("parameters", {})
    allowed = {"method", "url", "options", "authentication", "sendQuery", "queryParameters", "sendHeaders", "headerParameters"}
    unknown = set(p) - allowed
    if unknown:
        raise WorkflowError(f"Узел {name}: неподдерживаемые HTTP-параметры: {', '.join(sorted(unknown))}")
    if p.get("authentication", "none") != "none":
        raise WorkflowError(f"Узел {name}: HTTP-аутентификация пока не поддерживается")
    url = p.get("url")
    if not isinstance(url, str) or not url:
        raise WorkflowError(f"Узел {name}: требуется URL")
    method = p.get("method", "GET")
    if not isinstance(method, str) or not method.isalpha():
        raise WorkflowError(f"Узел {name}: некорректный HTTP-метод")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"timeout", "response"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые HTTP options")
    response = options.get("response", {})
    if not isinstance(response, dict) or set(response) - {"response"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки ответа")
    response = response.get("response", {})
    if not isinstance(response, dict) or set(response) - {"responseFormat"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки ответа")
    response_format = response.get("responseFormat", "json")
    if response_format not in ("json", "text", "file"):
        raise WorkflowError(f"Узел {name}: неподдерживаемый формат ответа {response_format!r}")
    timeout = options.get("timeout", 300000)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise WorkflowError(f"Узел {name}: некорректный timeout")
    tries = node.get("maxTries", 3) if node.get("retryOnFail", False) else 1
    delay = node.get("waitBetweenTries", 1000) if tries != 1 else 0
    if not isinstance(tries, int) or isinstance(tries, bool) or tries < 1 or not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
        raise WorkflowError(f"Узел {name}: некорректные настройки повторов")

    def expression(value: Any, parameter: str) -> str:
        if isinstance(value, str):
            return compile_expression(value, node=name, parameter=parameter).code
        return repr(value)

    def pairs(enabled: str, field: str) -> str:
        if not p.get(enabled, False):
            if field in p:
                raise WorkflowError(f"Узел {name}: {field} задан без {enabled}")
            return "{}"
        group = p.get(field, {})
        if not isinstance(group, dict) or set(group) != {"parameters"} or not isinstance(group["parameters"], list):
            raise WorkflowError(f"Узел {name}: некорректный {field}")
        entries = []
        for pair in group["parameters"]:
            if not isinstance(pair, dict) or set(pair) != {"name", "value"} or not isinstance(pair["name"], str):
                raise WorkflowError(f"Узел {name}: некорректный элемент {field}")
            entries.append(f"{pair['name']!r}: {expression(pair['value'], field)}")
        return "{" + ", ".join(entries) + "}"

    params = pairs("sendQuery", "queryParameters")
    headers = pairs("sendHeaders", "headerParameters")
    url_code = expression(url, "url")
    if response_format == "json":
        output = 'value = response.json()\n        values = value if isinstance(value, list) else [value]\n        result.extend({"json": value} for value in values)'
    elif response_format == "text":
        output = 'result.append({"json": {"data": response.text}})'
    else:
        output = 'result.append({"json": {}, "binary": {"data": {"data": response.content, "mimeType": response.headers.get("content-type", "application/octet-stream")}}})'
    return f'''import json
import time

import httpx


def {function_name}(items, results, execution):
    result = []
    for item in items:
        with httpx.Client(timeout={timeout / 1000!r}, follow_redirects=True) as client:
            for attempt in range({tries}):
                try:
                    response = client.request({method.upper()!r}, {url_code}, params={params}, headers={headers})
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt + 1 == {tries}:
                        raise
                    time.sleep({delay / 1000!r})
        {output}
    return result
'''
