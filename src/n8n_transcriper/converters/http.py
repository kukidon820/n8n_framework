"""Генерация HTTP-функций для поддерживаемых настроек n8n."""

import keyword
from typing import Any
from urllib.parse import urlsplit

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def generate_http_node(node: dict[str, Any], function_name: str) -> str:
    """Создать модуль HTTP-запроса; неизвестные настройки отклоняются."""
    name = node.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    p = node.get("parameters", {})
    allowed = {"method", "url", "options", "authentication", "nodeCredentialType", "sendQuery", "queryParameters", "sendHeaders", "headerParameters", "sendBody", "specifyBody", "jsonBody"}
    unknown = set(p) - allowed
    if unknown:
        raise WorkflowError(f"Узел {name}: неподдерживаемые HTTP-параметры: {', '.join(sorted(unknown))}")
    auth = p.get("authentication", "none")
    gmail = auth == "predefinedCredentialType" and p.get("nodeCredentialType") == "gmailOAuth2"
    if auth != "none" and not gmail:
        raise WorkflowError(f"Узел {name}: HTTP-аутентификация пока не поддерживается")
    if auth == "none" and "nodeCredentialType" in p:
        raise WorkflowError(f"Узел {name}: задан тип неиспользуемой авторизации")
    url = p.get("url")
    if not isinstance(url, str) or not url:
        raise WorkflowError(f"Узел {name}: требуется URL")
    method = p.get("method", "GET")
    if not isinstance(method, str) or not method.isalpha():
        raise WorkflowError(f"Узел {name}: некорректный HTTP-метод")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"timeout", "response", "batching", "allowUnauthorizedCerts", "redirect", "proxy", "lowercaseHeaders", "webdavMethods"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые HTTP options")
    for key in ("allowUnauthorizedCerts", "lowercaseHeaders", "webdavMethods"):
        if key in options and not isinstance(options[key], bool):
            raise WorkflowError(f"Узел {name}: {key} должен быть логическим")
    redirect = options.get("redirect")
    if redirect is not None and (not isinstance(redirect, dict) or set(redirect) != {"redirect"} or not isinstance(redirect["redirect"], dict) or set(redirect["redirect"]) - {"followRedirects", "maxRedirects"}):
        raise WorkflowError(f"Узел {name}: некорректные настройки redirect")
    version = node.get("typeVersion", 4)
    if not isinstance(version, (int, float)) or isinstance(version, bool):
        raise WorkflowError(f"Узел {name}: некорректная версия HTTP Request")
    redirection = redirect["redirect"] if redirect is not None else {}
    follow = redirection.get("followRedirects", version >= 4 if redirect is None else False)
    max_redirects = redirection.get("maxRedirects", 21)
    if not isinstance(follow, bool) or not isinstance(max_redirects, int) or isinstance(max_redirects, bool) or max_redirects < 0:
        raise WorkflowError(f"Узел {name}: некорректные настройки redirect")
    proxy = options.get("proxy")
    if proxy is not None and (not isinstance(proxy, str) or not proxy or urlsplit(proxy).username is not None or urlsplit(proxy).password is not None):
        raise WorkflowError(f"Узел {name}: прокси с credentials не поддерживается")
    batch = options.get("batching")
    batch_size, batch_interval = -1, 0
    if batch is not None:
        if not isinstance(batch, dict) or set(batch) != {"batch"} or not isinstance(batch["batch"], dict) or set(batch["batch"]) - {"batchSize", "batchInterval"}:
            raise WorkflowError(f"Узел {name}: некорректные настройки batching")
        batch_size = batch["batch"].get("batchSize", 50)
        batch_interval = batch["batch"].get("batchInterval", 1000)
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < -1 or not isinstance(batch_interval, (int, float)) or isinstance(batch_interval, bool) or batch_interval < 0:
            raise WorkflowError(f"Узел {name}: некорректный размер или интервал batching")
        if batch_size == 0:
            batch_size = 1
    response = options.get("response", {})
    if not isinstance(response, dict) or set(response) - {"response"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки ответа")
    response = response.get("response", {})
    if not isinstance(response, dict) or set(response) - {"responseFormat", "outputPropertyName"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки ответа")
    response_format = response.get("responseFormat", "autodetect")
    if response_format not in ("json", "text", "file", "autodetect"):
        raise WorkflowError(f"Узел {name}: неподдерживаемый формат ответа {response_format!r}")
    output_property = response.get("outputPropertyName", "data")
    if not isinstance(output_property, str) or not output_property or "." in output_property:
        raise WorkflowError(f"Узел {name}: некорректное имя поля ответа")
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
            if field == "headerParameters" and pair["name"].lower() in {"authorization", "proxy-authorization", "x-api-key", "cookie"}:
                raise WorkflowError(f"Узел {name}: секретный заголовок {pair['name']!r} требует отдельной поддержки credentials")
            entries.append(f"{pair['name']!r}: {expression(pair['value'], field)}")
        return "{" + ", ".join(entries) + "}"

    params = pairs("sendQuery", "queryParameters")
    headers = pairs("sendHeaders", "headerParameters")
    url_code = expression(url, "url")
    body = ""
    if p.get("sendBody", False):
        if p.get("specifyBody") != "json" or not isinstance(p.get("jsonBody"), str):
            raise WorkflowError(f"Узел {name}: поддерживается только JSON body")
        body_code = expression(p["jsonBody"], "jsonBody")
        body = f'''        body = {body_code}
        if not isinstance(body, str):
            raise TypeError("JSON body должен быть строкой")
        json.loads(body)
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
'''
    elif "jsonBody" in p or "specifyBody" in p:
        raise WorkflowError(f"Узел {name}: тело запроса задано без sendBody")
    auth_code = '''        if not all((config.GMAIL_CLIENT_ID, config.GMAIL_CLIENT_SECRET, config.GMAIL_REFRESH_TOKEN)):
            raise ValueError("Не заданы Gmail OAuth2 credentials")
        token_response = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": config.GMAIL_CLIENT_ID,
            "client_secret": config.GMAIL_CLIENT_SECRET,
            "refresh_token": config.GMAIL_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        }, timeout=30)
        token_response.raise_for_status()
        headers["Authorization"] = "Bearer " + token_response.json()["access_token"]
''' if gmail else ""
    json_output = 'value = response.json() if response.content.strip() else {}\n            values = value if isinstance(value, list) else [value]\n            result.extend({"json": value if value is not None else {}} for value in values)'
    text_output = f'result.append({{"json": {{{output_property!r}: response.text}}}})'
    file_output = f'''binary = dict(item.get("binary", {{}}))
            binary[{output_property!r}] = {{"data": response.content, "mimeType": response.headers.get("content-type", "application/octet-stream")}}
            result.append({{"json": item.get("json", {{}}), "binary": binary}})'''
    binary_types = ('image/', 'audio/', 'video/', 'application/octet-stream', 'application/gzip', 'application/zip', 'application/vnd.rar', 'application/epub+zip', 'application/x-bzip', 'application/x-bzip2', 'application/x-cdf', 'application/vnd.amazon.ebook', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.ms-fontobject', 'application/vnd.oasis.opendocument.presentation', 'application/pdf', 'application/x-tar', 'application/vnd.visio', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/x-7z-compressed')
    if response_format == "autodetect":
        detect = f'''        content_type = response.headers.get("content-type", "")
        response_format = "json" if "application/json" in content_type else ("file" if any(kind in content_type for kind in {binary_types!r}) else "text")
'''
    else:
        detect = f'        response_format = {response_format!r}\n'
    client_options = f'timeout={timeout / 1000!r}, follow_redirects={follow!r}, max_redirects={max_redirects!r}'
    if options.get("allowUnauthorizedCerts", False):
        client_options += ', verify=False'
    if proxy is not None:
        client_options += f', proxy={proxy!r}'
    throttle = f'        if item_index and item_index % {batch_size} == 0:\n            time.sleep({batch_interval / 1000!r})\n' if batch_size > 0 and batch_interval > 0 else ''
    lowercase = '        headers = {key.lower(): value for key, value in headers.items()}\n' if options.get("lowercaseHeaders", True) else ''
    return f'''import json
import time

import httpx
{"import config" if gmail else ""}


def {function_name}(items, results, execution):
    result = []
    for item_index, item in enumerate(items):
{throttle}
        headers = {headers}
{lowercase}{body}{auth_code}
        with httpx.Client({client_options}) as client:
            for attempt in range({tries}):
                try:
                    response = client.request({method.upper()!r}, {url_code}, params={params}, headers=headers{', content=body.encode("utf-8")' if body else ''})
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt + 1 == {tries}:
                        raise
                    time.sleep({delay / 1000!r})
{detect}        if response_format == "json":
            {json_output}
        elif response_format == "file":
            {file_output}
        else:
            {text_output}
    return result
'''
