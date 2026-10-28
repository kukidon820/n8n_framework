"""Генерация HTTP-функций для поддерживаемых настроек n8n."""

import hashlib
import keyword
import re
import textwrap
from typing import Any
from urllib.parse import urlsplit

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def http_credential_fields(node: dict[str, Any]) -> dict[str, str]:
    """Получить имена переменных для generic credentials без самих секретов."""
    params = node.get("parameters", {})
    if params.get("authentication", "none") != "genericCredentialType":
        return {}
    kind = params.get("genericAuthType")
    fields = {"httpBasicAuth": ("USERNAME", "PASSWORD"), "httpBearerAuth": ("TOKEN",),
              "httpHeaderAuth": ("HEADER_NAME", "HEADER_VALUE")}
    if kind not in fields:
        raise WorkflowError(f"Узел {node.get('name')}: неподдерживаемый тип HTTP credentials {kind!r}")
    credentials = node.get("credentials", {})
    credential = credentials.get(kind, {}) if isinstance(credentials, dict) else {}
    identifier = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(identifier, str) or not identifier:
        raise WorkflowError(f"Узел {node.get('name')}: отсутствует ID HTTP credentials {kind}")
    prefix = "HTTP_" + hashlib.sha256((kind + ':' + identifier).encode()).hexdigest()[:12].upper()
    return {field: f"{prefix}_{field}" for field in fields[kind]}


def generate_http_node(node: dict[str, Any], function_name: str) -> str:
    """Создать модуль HTTP-запроса; неизвестные настройки отклоняются."""
    name = node.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    p = node.get("parameters", {})
    allowed = {"method", "url", "options", "authentication", "nodeCredentialType", "genericAuthType", "sendQuery", "queryParameters", "sendHeaders", "headerParameters", "sendBody", "specifyBody", "jsonBody", "contentType", "bodyParameters", "body", "rawContentType", "inputDataFieldName"}
    unknown = set(p) - allowed
    if unknown:
        raise WorkflowError(f"Узел {name}: неподдерживаемые HTTP-параметры: {', '.join(sorted(unknown))}")
    auth = p.get("authentication", "none")
    gmail = auth == "predefinedCredentialType" and p.get("nodeCredentialType") == "gmailOAuth2"
    credential_fields = http_credential_fields(node)
    generic_type = p.get("genericAuthType") if credential_fields else None
    if auth != "none" and not gmail and not credential_fields:
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
    if not isinstance(options, dict) or set(options) - {"timeout", "response", "batching", "allowUnauthorizedCerts", "redirect", "proxy", "lowercaseHeaders", "webdavMethods", "pagination"}:
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
    if not isinstance(response, dict) or set(response) - {"responseFormat", "outputPropertyName", "fullResponse", "neverError"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки ответа")
    full_response = response.get("fullResponse", False)
    never_error = response.get("neverError", False)
    if not isinstance(full_response, bool) or not isinstance(never_error, bool):
        raise WorkflowError(f"Узел {name}: настройки ответа должны быть логическими")
    response_format = response.get("responseFormat", "autodetect")
    if response_format not in ("json", "text", "file", "autodetect"):
        raise WorkflowError(f"Узел {name}: неподдерживаемый формат ответа {response_format!r}")
    page_settings = options.get("pagination")
    page_config = None
    if page_settings is not None:
        if full_response or never_error:
            raise WorkflowError(f"Узел {name}: fullResponse и neverError с pagination пока не поддерживаются")
        if not isinstance(page_settings, dict) or set(page_settings) != {"pagination"} or not isinstance(page_settings["pagination"], dict):
            raise WorkflowError(f"Узел {name}: некорректная pagination")
        page_config = page_settings["pagination"]
        allowed_page = {"paginationMode", "parameters", "nextURL", "completeExpression", "paginationCompleteWhen", "limitPagesFetched", "maxRequests", "requestInterval"}
        page_mode = page_config.get("paginationMode")
        if set(page_config) - allowed_page or page_mode not in {"updateAParameterInEachRequest", "responseContainsNextURL"}:
            raise WorkflowError(f"Узел {name}: неподдерживаемый режим pagination")
        if method.upper() != "GET" or p.get("sendBody", False) or response_format != "json":
            raise WorkflowError(f"Узел {name}: pagination поддерживает GET с JSON-ответом")
        if page_mode == "updateAParameterInEachRequest":
            if page_config.get("paginationCompleteWhen", "responseIsEmpty") != "responseIsEmpty" or "nextURL" in page_config or "completeExpression" in page_config:
                raise WorkflowError(f"Узел {name}: неподдерживаемое завершение pagination")
            group = page_config.get("parameters", {})
            if not isinstance(group, dict) or set(group) != {"parameters"} or not isinstance(group["parameters"], list) or len(group["parameters"]) != 1:
                raise WorkflowError(f"Узел {name}: pagination требует один параметр query")
            entry = group["parameters"][0]
            if not isinstance(entry, dict) or set(entry) != {"type", "name", "value"} or entry.get("type") != "qs" or not isinstance(entry.get("name"), str) or not entry["name"]:
                raise WorkflowError(f"Узел {name}: некорректный параметр pagination")
            value = entry.get("value")
            if not isinstance(value, str) or not re.fullmatch(r"=?\s*{{\s*\$pageCount\s*(?:\+\s*1\s*)?}}", value):
                raise WorkflowError(f"Узел {name}: поддерживается только $pageCount или $pageCount + 1")
            page_offset = 1 if "+" in value else 0
            page_name = entry["name"]
        else:
            if auth != "none" or page_config.get("paginationCompleteWhen") != "other" or "parameters" in page_config:
                raise WorkflowError(f"Узел {name}: pagination по URL пока поддерживается без credentials и с условием other")
            next_url = page_config.get("nextURL")
            complete = page_config.get("completeExpression")
            match = re.fullmatch(r"=?\s*{{\s*\$response\.body((?:\.[A-Za-z_][A-Za-z_0-9]*)+)\s*}}", next_url) if isinstance(next_url, str) else None
            stop = re.fullmatch(r"=?\s*{{\s*!\s*\$response\.body((?:\.[A-Za-z_][A-Za-z_0-9]*)+)\s*}}", complete) if isinstance(complete, str) else None
            if not match or not stop or match.group(1) != stop.group(1):
                raise WorkflowError(f"Узел {name}: требуется nextURL и проверка отсутствия того же поля response.body")
            next_path = match.group(1).strip('.').split('.')
        max_pages = page_config.get("maxRequests", 100)
        page_interval = page_config.get("requestInterval", 0)
        if page_config.get("limitPagesFetched") is not True or not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1 or not isinstance(page_interval, (float, int)) or isinstance(page_interval, bool) or page_interval < 0:
            raise WorkflowError(f"Узел {name}: для pagination требуется положительный предел страниц и корректный интервал")
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
    body_argument = ""
    if p.get("sendBody", False):
        content_type = p.get("contentType", "json")
        mode = p.get("specifyBody", "keypair")
        body_fields = {"jsonBody", "bodyParameters", "body", "rawContentType", "inputDataFieldName"}
        allowed_body = {"json": ({"jsonBody"} if mode == "json" else {"bodyParameters"}),
                        "form-urlencoded": {"bodyParameters"}, "multipart-form-data": {"bodyParameters"},
                        "binaryData": {"inputDataFieldName"}, "raw": {"body", "rawContentType"}}
        if set(p) & body_fields - allowed_body.get(content_type, set()):
            raise WorkflowError(f"Узел {name}: поля тела не соответствуют выбранному формату")
        if content_type == "json" and mode == "json":
            if not isinstance(p.get("jsonBody"), str):
                raise WorkflowError(f"Узел {name}: требуется jsonBody")
            body_code = expression(p["jsonBody"], "jsonBody")
            body = f'''        body = {body_code}
        if isinstance(body, str):
            json.loads(body)
        else:
            body = json.dumps(body, ensure_ascii=False)
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
'''
            body_argument = ', content=body.encode("utf-8")'
        elif content_type in {"json", "form-urlencoded", "multipart-form-data"} and (content_type == "multipart-form-data" or mode == "keypair"):
            group = p.get("bodyParameters", {"parameters": []})
            if not isinstance(group, dict) or set(group) != {"parameters"} or not isinstance(group["parameters"], list):
                raise WorkflowError(f"Узел {name}: некорректные bodyParameters")
            entries = []
            seen = set()
            for pair in group["parameters"]:
                if not isinstance(pair, dict) or set(pair) - {"name", "value", "parameterType", "inputDataFieldName"}:
                    raise WorkflowError(f"Узел {name}: некорректное поле bodyParameters")
                key = pair.get("name")
                if not isinstance(key, str) or not key or (content_type == "json" and ('.' in key or '[' in key)):
                    raise WorkflowError(f"Узел {name}: неподдерживаемое имя поля тела {key!r}")
                if key in seen and content_type != "multipart-form-data":
                    raise WorkflowError(f"Узел {name}: повторяющееся поле тела {key!r}")
                seen.add(key)
                kind = pair.get("parameterType", "formData")
                if kind == "formBinaryData" and content_type == "multipart-form-data":
                    field = pair.get("inputDataFieldName")
                    if not isinstance(field, str) or not field or field.startswith("="):
                        raise WorkflowError(f"Узел {name}: требуется inputDataFieldName")
                    entries.append(f'({key!r}, (item["binary"][{field!r}].get("fileName", "file"), item["binary"][{field!r}]["data"], item["binary"][{field!r}].get("mimeType", "application/octet-stream")))')
                elif kind == "formData" and "value" in pair:
                    val = expression(pair["value"], f"bodyParameters.{key}")
                    entries.append(f'({key!r}, (None, {val}))' if content_type == "multipart-form-data" else f'({key!r}, {val})')
                else:
                    raise WorkflowError(f"Узел {name}: неподдерживаемый тип поля {key!r}")
            values = '[' + ', '.join(entries) + ']'
            if content_type == "multipart-form-data":
                if not entries:
                    raise WorkflowError(f"Узел {name}: пустой multipart пока не поддерживается")
                body = f'        body = {values}\n'
                body_argument = ', files=body'
            elif content_type == "form-urlencoded":
                body = f'''        body = urlencode({values}, doseq=True).encode("utf-8")
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/x-www-form-urlencoded"
'''
                body_argument = ', content=body'
            else:
                body = f'''        body = json.dumps(dict({values}), ensure_ascii=False).encode("utf-8")
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = "application/json"
'''
                body_argument = ', content=body'
        elif content_type == "binaryData":
            field = p.get("inputDataFieldName")
            if not isinstance(field, str) or not field or field.startswith("="):
                raise WorkflowError(f"Узел {name}: требуется inputDataFieldName")
            body = f'''        binary_file = item["binary"][{field!r}]
        body = binary_file["data"]
        if not any(key.lower() == "content-type" for key in headers):
            headers["Content-Type"] = binary_file.get("mimeType", "application/octet-stream")
'''
            body_argument = ', content=body'
        elif content_type == "raw" and isinstance(p.get("body"), str) and isinstance(p.get("rawContentType"), str):
            body = f'''        body = {expression(p['body'], 'body')}.encode("utf-8")
        headers["Content-Type"] = {p['rawContentType']!r}
'''
            body_argument = ', content=body'
        else:
            raise WorkflowError(f"Узел {name}: неподдерживаемый формат тела {content_type!r}")
    elif set(p) & {"jsonBody", "specifyBody", "contentType", "bodyParameters", "body", "rawContentType", "inputDataFieldName"}:
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
    if credential_fields:
        values = ', '.join('config.' + value for value in credential_fields.values())
        auth_code += f'        if not all(({values},)):\n            raise ValueError({("Не заданы HTTP credentials для узла " + name)!r})\n'
        if generic_type == "httpBearerAuth":
            auth_code += f'        headers["Authorization"] = "Bearer " + config.{credential_fields["TOKEN"]}\n'
        elif generic_type == "httpHeaderAuth":
            auth_code += f'        headers[config.{credential_fields["HEADER_NAME"]}] = config.{credential_fields["HEADER_VALUE"]}\n'
    json_output = 'value = response.json() if response.content.strip() else {}\n            values = value if isinstance(value, list) else [value]\n            result.extend({"json": value if value is not None else {}} for value in values)'
    text_output = f'result.append({{"json": {{{output_property!r}: response.text}}}})'
    file_output = f'''binary = dict(item.get("binary", {{}}))
            binary[{output_property!r}] = {{"data": response.content, "mimeType": response.headers.get("content-type", "application/octet-stream")}}
            result.append({{"json": item.get("json", {{}}), "binary": binary}})'''
    if full_response:
        metadata = '{"headers": dict(response.headers), "statusCode": response.status_code, "statusMessage": response.reason_phrase}'
        json_output = f'body_value = response.json() if response.content.strip() else {{}}\n            result.append({{"json": {{**{metadata}, "body": body_value}}}})'
        text_output = f'result.append({{"json": {{**{metadata}, {output_property!r}: response.text}}}})'
        file_output = f'''binary = dict(item.get("binary", {{}}))
            binary[{output_property!r}] = {{"data": response.content, "mimeType": response.headers.get("content-type", "application/octet-stream")}}
            result.append({{"json": {metadata}, "binary": binary}})'''
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
    if generic_type == "httpBasicAuth":
        client_options += f', auth=(config.{credential_fields["USERNAME"]}, config.{credential_fields["PASSWORD"]})'
    redirect_guard = ""
    if follow and (gmail or credential_fields):
        secret_header = f'config.{credential_fields["HEADER_NAME"]}' if generic_type == "httpHeaderAuth" else '"Authorization"'
        redirect_guard = f'''        def origin(url):
            parts = urlsplit(str(url))
            return (parts.scheme.lower(), parts.hostname, parts.port or (443 if parts.scheme.lower() == "https" else 80))
        original_origin = origin(request_url)
        def strip_credentials_on_redirect(request):
            if origin(request.url) != original_origin:
                request.headers.pop({secret_header}, None)
'''
        client_options += ', event_hooks={"request": [strip_credentials_on_redirect]}'
    throttle = f'        if item_index and item_index % {batch_size} == 0:\n            time.sleep({batch_interval / 1000!r})\n' if batch_size > 0 and batch_interval > 0 else ''
    lowercase = '        headers = {key.lower(): value for key, value in headers.items()}\n' if options.get("lowercaseHeaders", True) else ''
    request_block = f'''        with httpx.Client({client_options}) as client:
            for attempt in range({tries}):
                try:
                    response = client.request({method.upper()!r}, request_url, params=page_params, headers=headers{body_argument})
                    {"pass" if never_error else "response.raise_for_status()"}
                    break
                except httpx.HTTPError:
                    if attempt + 1 == {tries}:
                        raise
                    time.sleep({delay / 1000!r})
'''
    output_block = f'''{detect}        if response_format == "json":
            {json_output}
        elif response_format == "file":
            {file_output}
        else:
            {text_output}
'''
    if page_config is not None and page_mode == "updateAParameterInEachRequest":
        fetch = (f'        for page_index in range({max_pages}):\n'
                 f'            if page_index and {page_interval!r}:\n                time.sleep({page_interval / 1000!r})\n'
                 f'            page_params = dict({params})\n            page_params[{page_name!r}] = page_index + {page_offset}\n' +
                 textwrap.indent(request_block, '    ') +
                 '            page_data = response.json() if response.content.strip() else []\n'
                 '            if not isinstance(page_data, list):\n                raise TypeError("Pagination responseIsEmpty поддерживает только массив JSON")\n'
                 '            if not page_data:\n                break\n' + textwrap.indent(output_block, '    '))
    elif page_config is not None:
        fetch = (f'        for page_index in range({max_pages}):\n'
                 f'            if page_index and {page_interval!r}:\n                time.sleep({page_interval / 1000!r})\n'
                 f'            page_params = dict({params})\n' + textwrap.indent(request_block, '    ') +
                 '            page_data = response.json() if response.content.strip() else {}\n'
                 '            if not isinstance(page_data, dict):\n                raise TypeError("Pagination ожидает JSON-объект")\n' +
                 textwrap.indent(output_block, '    ') +
                 f'            next_url = page_data\n            for key in {next_path!r}:\n'
                 '                if not isinstance(next_url, dict):\n                    raise TypeError("Некорректный путь nextURL")\n'
                 '                next_url = next_url.get(key)\n'
                 '            if next_url is None or next_url is False or next_url == "" or next_url == 0:\n                break\n'
                 '            if not isinstance(next_url, str) or urlsplit(next_url).scheme not in ("http", "https") or not urlsplit(next_url).hostname:\n'
                 '                raise ValueError("Некорректный nextURL")\n'
                 '            request_url = next_url\n')
    else:
        fetch = f'        page_params = {params}\n' + request_block + output_block
    return f'''import json
import time
from urllib.parse import urlencode, urlsplit

import httpx
{"import config" if gmail or credential_fields else ""}


def {function_name}(items, results, execution):
    result = []
    for item_index, item in enumerate(items):
{throttle}
        headers = {headers}
        request_url = {url_code}
{lowercase}{body}{auth_code}{redirect_guard}
{fetch}
    return result
'''
