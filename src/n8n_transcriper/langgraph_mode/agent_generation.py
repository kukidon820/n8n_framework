"""Сгенерировать AI-агентов для workflow, конвертируемых с ``--langgraph``."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from n8n_transcriper.converters.ai import generate_ai_agent
from n8n_transcriper.converters.http import (
    http_credential_fields,
    http_secret_header_fields,
)
from n8n_transcriper.langgraph_mode.detection import AiRequestDetection
from n8n_transcriper.langgraph_mode.expressions import compile_langgraph_expression
from n8n_transcriper.langgraph_mode.providers import endpoint_base_url
from n8n_transcriper.reader import WorkflowError


_MODEL_PACKAGES = {
    "openai": "langchain-openai",
    "deepseek": "langchain-openai",
    "openrouter": "langchain-openai",
    "openai_compatible": "langchain-openai",
    "anthropic": "langchain-anthropic",
    "gemini": "langchain-google-genai",
    "gemini_compatible": "langchain-google-genai",
    "ollama": "langchain-ollama",
}
_MODEL_CLASSES = {
    "openai": ("langchain_openai", "ChatOpenAI"),
    "deepseek": ("langchain_openai", "ChatOpenAI"),
    "openrouter": ("langchain_openai", "ChatOpenAI"),
    "openai_compatible": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "gemini": ("langchain_google_genai", "ChatGoogleGenerativeAI"),
    "gemini_compatible": ("langchain_google_genai", "ChatGoogleGenerativeAI"),
    "ollama": ("langchain_ollama", "ChatOllama"),
}
_PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434",
}


def _literal_or_expression(value: Any, node_name: str, parameter: str) -> tuple[str, set[str]]:
    """Преобразовать JSON-литералы и expressions n8n в исходный Python-код."""
    if isinstance(value, str) and (value.startswith("=") or "{{" in value):
        expression = compile_langgraph_expression(
            value, node=node_name, parameter=parameter
        )
        return expression.code, set(expression.dependencies)
    if isinstance(value, dict):
        pairs = []
        dependencies: set[str] = set()
        for key, nested in value.items():
            code, refs = _literal_or_expression(nested, node_name, f"{parameter}.{key}")
            pairs.append(f"{key!r}: {code}")
            dependencies.update(refs)
        return "{" + ", ".join(pairs) + "}", dependencies
    if isinstance(value, list):
        parts = []
        dependencies = set()
        for index, nested in enumerate(value):
            code, refs = _literal_or_expression(nested, node_name, f"{parameter}[{index}]")
            parts.append(code)
            dependencies.update(refs)
        return "[" + ", ".join(parts) + "]", dependencies
    return repr(value), set()


def _request_body(node: dict[str, Any]) -> tuple[str, set[str]]:
    """Скомпилировать JSON-тело HTTP-узла в одно выражение Python."""
    name = str(node.get("name") or "HTTP Request")
    parameters = node.get("parameters", {})
    value = parameters.get("jsonBody")
    if value is None:
        value = parameters.get("body")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return _literal_or_expression(value, name, "jsonBody")
        value = decoded
    if isinstance(value, (dict, list)):
        return _literal_or_expression(value, name, "jsonBody")

    group = parameters.get("bodyParameters", {})
    pairs = group.get("parameters", []) if isinstance(group, dict) else []
    if isinstance(pairs, list) and pairs:
        body: dict[str, Any] = {}
        for pair in pairs:
            if not isinstance(pair, dict) or not isinstance(pair.get("name"), str):
                raise WorkflowError(f"Узел {name}: некорректные JSON bodyParameters для AI-запроса")
            body[pair["name"]] = pair.get("value")
        return _literal_or_expression(body, name, "bodyParameters")
    raise WorkflowError(f"Узел {name}: не удалось получить JSON-тело запроса AI-модели")


def _base_url(url: str, detection: AiRequestDetection) -> str:
    """Получить настраиваемый базовый адрес провайдера без секретов в URL."""
    endpoint = detection.endpoint
    if endpoint is not None:
        if endpoint.provider == "gemini":
            parsed = urlsplit(url)
            return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        return endpoint_base_url(url, endpoint)
    if detection.provider == "gemini_compatible":
        return ""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parent, "", ""))


def _model_from_url(url: str) -> str:
    """Извлечь идентификатор модели Gemini из REST-пути."""
    path = urlsplit(url).path
    match = re.search(r"/models/([^/:]+):(?:generateContent|streamGenerateContent)$", path, re.I)
    return unquote(match.group(1)) if match else ""


def _http_auth_defaults(node: dict[str, Any]) -> tuple[str, str, str]:
    """Получить ключ настройки, имя заголовка и его префикс для config.py."""
    parameters = node.get("parameters", {})
    auth_kind = parameters.get("genericAuthType")
    fields = http_credential_fields(node)
    raw_url = parameters.get("url", "")
    query_names = {key.casefold() for key, _ in parse_qsl(urlsplit(raw_url).query)} if isinstance(raw_url, str) else set()
    host = (urlsplit(raw_url).hostname or "").casefold() if isinstance(raw_url, str) else ""
    if host == "generativelanguage.googleapis.com" and query_names & {
        "key", "api_key", "apikey"
    }:
        return "", "x-goog-api-key", ""
    if auth_kind == "httpBearerAuth" and "TOKEN" in fields:
        return f"config.{fields['TOKEN']}", "Authorization", "Bearer "
    if auth_kind == "httpHeaderAuth" and "HEADER_VALUE" in fields:
        return f"config.{fields['HEADER_VALUE']}", "", ""
    if auth_kind == "httpQueryAuth" and "VALUE" in fields:
        return f"config.{fields['VALUE']}", "Authorization", "Bearer "
    if auth_kind == "httpBasicAuth" and {"USERNAME", "PASSWORD"} <= fields.keys():
        return "", "Authorization", "Basic "

    header_fields = http_secret_header_fields(node)
    entries = parameters.get("headerParameters", {}).get("parameters", [])
    for index, field in header_fields.items():
        if index >= len(entries):
            continue
        header_name = str(entries[index].get("name", ""))
        if header_name.casefold() in {
            "authorization", "x-api-key", "api-key", "x-goog-api-key"
        }:
            prefix = "Bearer " if header_name.casefold() == "authorization" else ""
            return f"config.{field}", header_name, prefix
    return "", "Authorization", "Bearer "


def _header_code(node: dict[str, Any]) -> tuple[str, set[str]]:
    """Сгенерировать заголовки запроса и вынести секреты в config.py."""
    parameters = node.get("parameters", {})
    entries = parameters.get("headerParameters", {}).get("parameters", [])
    secret_fields = http_secret_header_fields(node)
    values: list[str] = []
    dependencies: set[str] = set()
    for index, pair in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(pair, dict) or not isinstance(pair.get("name"), str):
            continue
        if index in secret_fields:
            code = f"config.{secret_fields[index]}"
        else:
            code, refs = _literal_or_expression(
                pair.get("value", ""), str(node.get("name") or "HTTP Request"),
                f"headerParameters[{index}].value",
            )
            dependencies.update(refs)
        values.append(f"{pair['name']!r}: {code}")

    auth_kind = node.get("parameters", {}).get("genericAuthType")
    fields = http_credential_fields(node)
    if auth_kind == "httpBearerAuth" and "TOKEN" in fields:
        values.append(f"'Authorization': 'Bearer ' + config.{fields['TOKEN']}")
    elif auth_kind == "httpHeaderAuth" and "HEADER_VALUE" in fields:
        values.append(
            f"config.{fields['HEADER_NAME']}: config.{fields['HEADER_VALUE']}"
        )
    elif auth_kind == "httpBasicAuth" and {"USERNAME", "PASSWORD"} <= fields.keys():
        values.append(
            "'Authorization': 'Basic ' + base64.b64encode("
            f"(config.{fields['USERNAME']} + ':' + config.{fields['PASSWORD']}).encode()"
            ").decode()"
        )
    return "headers = {" + ", ".join(values) + "}", dependencies


def _safe_endpoint_url(url: str) -> str:
    """Удалить credentials из URL перед записью собственного endpoint в config.py."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    secret_query_names = {
        "key", "api_key", "apikey", "token", "access_token", "client_secret",
        "secret", "authorization", "x-api-key",
    }
    safe_query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if name.casefold() not in secret_query_names
        ]
    )
    return urlunsplit((parsed.scheme, host, parsed.path, safe_query, ""))


def _generate_openai_compatible_endpoint_node(
    node: dict[str, Any], module_name: str
) -> tuple[str, str, set[str]]:
    """Сгенерировать модель LangGraph, отправляющую запрос на исходный URL."""
    node_name = str(node.get("name") or "HTTP Request")
    parameters = node.get("parameters", {})
    body_code, references = _request_body(node)
    header_code, header_references = _header_code(node)
    references.update(header_references)
    prefix = f"LANGGRAPH_HTTP_{re.sub(r'[^A-Z0-9]+', '_', module_name.upper()).strip('_')}"
    endpoint = _safe_endpoint_url(str(parameters.get("url", "")))
    default_key, default_header, default_prefix = _http_auth_defaults(node)
    api_key_default = default_key.removeprefix("config.") if default_key else "''"
    config_block = (
        f"\n# Параметры собственного OpenAI-совместимого endpoint HTTP-узла {node_name!r}.\n"
        f"{prefix}_ENDPOINT_URL = os.getenv({(prefix + '_ENDPOINT_URL')!r}, {endpoint!r})\n"
        f"{prefix}_API_KEY = os.getenv({(prefix + '_API_KEY')!r}, {api_key_default})\n"
        f"{prefix}_API_KEY_HEADER = os.getenv({(prefix + '_API_KEY_HEADER')!r}, {default_header!r})\n"
        f"{prefix}_API_KEY_PREFIX = os.getenv({(prefix + '_API_KEY_PREFIX')!r}, {default_prefix!r})\n"
    )
    source = f'''"""Собственная OpenAI-совместимая модель LangGraph для HTTP-узла {node_name!r}."""

from typing import Any

import base64
import httpx
import config
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

{_message_helpers()}


class WorkflowEndpointChatModel(BaseChatModel):
    """Асинхронно обращаться к собственному endpoint исходного workflow."""

    model: str
    endpoint: str
    request_headers: dict[str, str]
    request_options: dict[str, Any]
    timeout: float = 180.0

    @property
    def _llm_type(self) -> str:
        return "workflow-openai-compatible-endpoint"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("Сгенерированная модель endpoint поддерживает только async-вызовы")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        request_messages = []
        for message in messages:
            if isinstance(message, SystemMessage):
                role = "system"
            elif isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, ToolMessage):
                role = "tool"
            elif isinstance(message, AIMessage):
                role = "assistant"
            else:
                raise TypeError(f"Неподдерживаемый тип сообщения LangChain: {{type(message).__name__}}")
            entry = {{"role": role, "content": message.content}}
            if isinstance(message, ToolMessage):
                entry["tool_call_id"] = message.tool_call_id
            request_messages.append(entry)

        request_body = dict(self.request_options)
        request_body["model"] = self.model
        request_body["messages"] = request_messages
        if stop is not None:
            request_body["stop"] = stop

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers=self.request_headers,
                json=request_body,
            )
        response.raise_for_status()
        response_body = response.json()
        try:
            content = response_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                "Собственный AI endpoint должен вернуть OpenAI-совместимый "
                "ответ choices[0].message.content"
            ) from exc
        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])


async def run_node(items, results, execution):
    """Отправить каждый собранный prompt агенту по исходному HTTP-адресу."""
    output = []
    for item in items:
        payload = {body_code}
        if not isinstance(payload, dict):
            raise TypeError("После вычисления AI-тело запроса должно быть JSON-объектом")
        {header_code}
        model_name = payload.get("model")
        if not isinstance(model_name, str) or not model_name:
            raise ValueError("Узел {node_name}: укажите поле model в JSON body")

        api_key = config.{prefix}_API_KEY
        header_name = config.{prefix}_API_KEY_HEADER
        header_prefix = config.{prefix}_API_KEY_PREFIX
        header_value = headers.get(header_name) or next(
            (value for name, value in headers.items()
             if name.casefold() in {{"authorization", "x-api-key", "api-key", "x-goog-api-key"}}),
            None,
        )
        if header_value:
            api_key = str(header_value)
            if header_prefix and api_key.casefold().startswith(header_prefix.casefold()):
                api_key = api_key[len(header_prefix):]
            elif api_key.casefold().startswith("bearer "):
                api_key = api_key[7:]
        elif api_key and header_prefix:
            headers[header_name] = f"{{header_prefix}}{{api_key}}"
        request_options = {{
            key: value for key, value in payload.items()
            if key not in {{"model", "messages"}}
        }}
        model = WorkflowEndpointChatModel(
            model=model_name,
            endpoint=config.{prefix}_ENDPOINT_URL,
            request_headers=headers,
            request_options=request_options,
        )
        agent = create_agent(model=model, tools=[])
        result = await agent.ainvoke({{"messages": _messages_from_payload(payload)}})
        messages = result.get("messages", [])
        if not messages:
            raise ValueError("Агент LangGraph не вернул ответ модели")
        text = _message_text(messages[-1])
        output.append({{"json": _response_body("openai_compatible", text, model_name)}})
    return output
'''
    return source, config_block, references


def _provider_import(provider: str) -> str:
    module, class_name = _MODEL_CLASSES[provider]
    return f"from {module} import {class_name}"


def _model_constructor(provider: str, *, model: str, base_url: str, key: str, options: str) -> str:
    """Сгенерировать вызов конструктора chat-модели выбранного провайдера."""
    if provider in {"openai", "deepseek", "openrouter", "openai_compatible"}:
        return (
            f"ChatOpenAI(model={model}, base_url={base_url}, api_key={key}, "
            f"default_headers=headers or None, **{options})"
        )
    if provider == "anthropic":
        return (
            f"ChatAnthropic(model={model}, api_key={key}, base_url={base_url} or None, "
            f"default_headers=headers or None, **{options})"
        )
    if provider in {"gemini", "gemini_compatible"}:
        return (
            f"ChatGoogleGenerativeAI(model={model}, api_key={key}, "
            f"base_url={base_url} or None, additional_headers=headers or None, **{options})"
        )
    if provider == "ollama":
        return f"ChatOllama(model={model}, base_url={base_url}, **{options})"
    raise WorkflowError(f"Неизвестный AI-провайдер LangGraph: {provider}")


def _message_helpers() -> str:
    """Сгенерировать небольшие функции преобразования тела провайдера в сообщения."""
    return '''
def _messages_from_payload(payload):
    system = payload.get("system")
    instruction = payload.get("systemInstruction")
    if not system and isinstance(instruction, dict):
        parts = instruction.get("parts", [])
        system = "".join(
            part.get("text", "") for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )

    messages = payload.get("messages")
    if isinstance(messages, list):
        result = []
        for message in messages:
            if not isinstance(message, dict) or "content" not in message:
                raise ValueError("В AI-теле запроса у сообщения отсутствует поле content")
            role = message.get("role", "user")
            if role == "model":
                role = "assistant"
            result.append({"role": role, "content": message["content"]})
        if system and not any(message["role"] == "system" for message in result):
            result.insert(0, {"role": "system", "content": system})
        return result

    contents = payload.get("contents")
    if isinstance(contents, list):
        result = []
        for content in contents:
            if not isinstance(content, dict):
                raise ValueError("Поле Gemini contents должно содержать объекты")
            role = "assistant" if content.get("role") == "model" else "user"
            parts = content.get("parts", [])
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
            result.append({"role": role, "content": text})
        if system:
            result.insert(0, {"role": "system", "content": system})
        return result

    prompt = payload.get("prompt", payload.get("input"))
    if prompt is not None:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        result.append({"role": "user", "content": prompt})
        return result
    raise ValueError("В AI-теле запроса нет messages, contents или prompt")


def _message_text(message):
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return str(content or "")


def _response_body(provider, text, model):
    if provider == "anthropic":
        return {"content": [{"type": "text", "text": text}], "model": model, "role": "assistant"}
    if provider in {"gemini", "gemini_compatible"}:
        return {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}}], "model": model}
    if provider == "ollama":
        return {"message": {"role": "assistant", "content": text}, "model": model, "done": True}
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "model": model}
'''


def _generate_gemini_interactions_node(
    node: dict[str, Any], module_name: str
) -> tuple[str, str, set[str]]:
    """Сгенерировать асинхронный адаптер для API моделей и агентов Gemini."""
    node_name = str(node.get("name") or "HTTP Request")
    body_code, references = _request_body(node)
    header_code, header_references = _header_code(node)
    references.update(header_references)
    prefix = f"LANGGRAPH_HTTP_{re.sub(r'[^A-Z0-9]+', '_', module_name.upper()).strip('_')}"
    url = str(node.get("parameters", {}).get("url", ""))
    api_version = "v1beta" if "/v1beta/" in url.casefold() else "v1"
    default_key, default_header, default_prefix = _http_auth_defaults(node)
    api_key_default = default_key.removeprefix("config.") if default_key else "''"
    config_block = (
        f"\n# Параметры Gemini Interactions для HTTP-узла {node_name!r}.\n"
        f"{prefix}_API_KEY = os.getenv({(prefix + '_API_KEY')!r}, {api_key_default})\n"
        f"{prefix}_API_KEY_HEADER = os.getenv({(prefix + '_API_KEY_HEADER')!r}, {default_header!r})\n"
        f"{prefix}_API_KEY_PREFIX = os.getenv({(prefix + '_API_KEY_PREFIX')!r}, {default_prefix!r})\n"
        f"{prefix}_API_VERSION = os.getenv({(prefix + '_API_VERSION')!r}, {api_version!r})\n"
    )
    source = f'''"""Адаптер Gemini Interactions API для HTTP-узла {node_name!r}."""

from google import genai
from google.genai import types
import config


async def run_node(items, results, execution):
    """Вызвать модель или управляемого агента Gemini через async SDK."""
    output = []
    for item in items:
        payload = {body_code}
        if not isinstance(payload, dict):
            raise TypeError("После вычисления тело Gemini Interactions должно быть JSON-объектом")
        {header_code}
        api_key = config.{prefix}_API_KEY
        header_name = config.{prefix}_API_KEY_HEADER
        header_prefix = config.{prefix}_API_KEY_PREFIX
        header_value = headers.get(header_name) or next(
            (value for name, value in headers.items()
             if name.casefold() in {{"authorization", "x-goog-api-key", "x-api-key", "api-key"}}),
            None,
        )
        if header_value:
            api_key = str(header_value)
            if header_prefix and api_key.casefold().startswith(header_prefix.casefold()):
                api_key = api_key[len(header_prefix):]
            elif api_key.casefold().startswith("bearer "):
                api_key = api_key[7:]
        if not api_key:
            raise ValueError("Заполните {prefix}_API_KEY в config.py или переменную окружения")
        if payload.get("stream"):
            raise ValueError("Потоковый Gemini Interactions ответ нужно обработать вручную")
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version=config.{prefix}_API_VERSION),
        )
        async_client = client.aio
        try:
            extra_headers = {{
                name: value for name, value in headers.items()
                if name.casefold() not in {{"authorization", "x-goog-api-key"}}
            }}
            interaction = await async_client.interactions.create(
                **payload,
                extra_headers=extra_headers or None,
            )
        finally:
            try:
                await async_client.aclose()
            finally:
                client.close()
        if hasattr(interaction, "model_dump"):
            response_body = interaction.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        else:
            response_body = dict(interaction)
        output_text = getattr(interaction, "output_text", None)
        if output_text is not None:
            response_body.setdefault("output_text", output_text)
        output.append({{"json": response_body}})
    return output
'''
    return source, config_block, references


def generate_http_agent_node(
    node: dict[str, Any],
    detection: AiRequestDetection,
    module_name: str,
) -> tuple[str, str, set[str]]:
    """Заменить распознанный вызов модели HTTP-узла агентом LangGraph провайдера."""
    if not detection.is_ai or detection.provider not in _MODEL_PACKAGES:
        raise WorkflowError("Для HTTP-узла не удалось выбрать поддержку LangChain ChatModel")
    if detection.provider == "openai_compatible" and detection.endpoint is None:
        return _generate_openai_compatible_endpoint_node(node, module_name)
    if detection.provider == "gemini" and detection.operation == "interactions":
        return _generate_gemini_interactions_node(node, module_name)
    node_name = str(node.get("name") or "HTTP Request")
    provider = detection.provider
    body_code, references = _request_body(node)
    header_code, header_references = _header_code(node)
    references.update(header_references)
    prefix = f"LANGGRAPH_HTTP_{re.sub(r'[^A-Z0-9]+', '_', module_name.upper()).strip('_')}"
    url = str(node.get("parameters", {}).get("url", ""))
    base_url = _base_url(url, detection) or _PROVIDER_DEFAULTS.get(provider, "")
    default_key, default_header, default_prefix = _http_auth_defaults(node)
    # Значения добавляются в config.py, поэтому значения по умолчанию должны
    # ссылаться на переменные напрямую, а не импортировать config.py в себя.
    api_key_default = default_key.removeprefix("config.") if default_key else "''"
    config_block = (
        f"\n# Параметры модели LangGraph для HTTP-узла {node_name!r}.\n"
        f"{prefix}_BASE_URL = os.getenv({(prefix + '_BASE_URL')!r}, {base_url!r})\n"
        f"{prefix}_API_KEY = os.getenv({(prefix + '_API_KEY')!r}, {api_key_default})\n"
        f"{prefix}_API_KEY_HEADER = os.getenv({(prefix + '_API_KEY_HEADER')!r}, {default_header!r})\n"
        f"{prefix}_API_KEY_PREFIX = os.getenv({(prefix + '_API_KEY_PREFIX')!r}, {default_prefix!r})\n"
    )
    api_key_header = ""
    model_import = _provider_import(provider)
    constructor = _model_constructor(
        provider,
        model="model_name",
        base_url=f"config.{prefix}_BASE_URL",
        key="api_key",
        options="options",
    )
    if provider == "anthropic":
        api_key_header = "x-api-key"
    elif provider in {"gemini", "gemini_compatible"}:
        api_key_header = "x-goog-api-key"

    source = f'''"""Агент LangGraph, созданный из HTTP Request n8n {node_name!r}."""

import base64
import json
import config
import re
from langchain.agents import create_agent
{model_import}

{_message_helpers()}


async def run_node(items, results, execution):
    """Выполнить запрос к модели через агент LangChain выбранного провайдера."""
    output = []
    for item in items:
        {{body_line}}
        if not isinstance(payload, dict):
            raise TypeError("После вычисления AI-тело запроса должно быть JSON-объектом")
        model_name = payload.get("model") or {_model_from_url(url)!r}
        if not isinstance(model_name, str) or not model_name:
            raise ValueError("Узел {node_name}: укажите поле model в JSON body")
        {header_code}
        api_key = config.{prefix}_API_KEY
        header_name = config.{prefix}_API_KEY_HEADER
        header_prefix = config.{prefix}_API_KEY_PREFIX
        header_value = headers.get(header_name) or next(
            (value for name, value in headers.items() if name.casefold() in {{"authorization", "x-api-key", "api-key", "x-goog-api-key"}}),
            None,
        )
        if header_value:
            api_key = str(header_value)
            if header_name.casefold() == "authorization" and header_prefix and api_key.casefold().startswith(header_prefix.casefold()):
                api_key = api_key[len(header_prefix):]
            elif header_name.casefold() == "authorization" and api_key.casefold().startswith("bearer "):
                api_key = api_key[7:]
        elif api_key and header_prefix:
            headers[header_name] = f"{{header_prefix}}{{api_key}}"
        if {provider!r} != "ollama" and not api_key:
            raise ValueError("Заполните {prefix}_API_KEY в config.py или переменную окружения")
        options = _model_options(payload, {provider!r})
        model = {constructor}
        agent = create_agent(model=model, tools=[])
        result = await agent.ainvoke({{"messages": _messages_from_payload(payload)}})
        messages = result.get("messages", [])
        if not messages:
            raise ValueError("Агент LangGraph не вернул ответ модели")
        text = _message_text(messages[-1])
        output.append({{"json": _response_body({provider!r}, text, model_name)}})
    return output


def _model_options(payload, provider):
    values = dict(payload)
    if provider in {"gemini", "gemini_compatible"}:
        generation_config = payload.get("generationConfig")
        if isinstance(generation_config, dict):
            values.update(generation_config)
        aliases = {{
            "maxOutputTokens": "max_output_tokens",
            "candidateCount": "candidate_count",
            "stopSequences": "stop",
            "frequencyPenalty": "frequency_penalty",
            "presencePenalty": "presence_penalty",
        }}
        for source_name, model_name in aliases.items():
            if source_name in values:
                values[model_name] = values[source_name]
    elif provider == "ollama":
        provider_options = payload.get("options")
        if isinstance(provider_options, dict):
            values.update(provider_options)

    source_to_model = {{
        "openai": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "deepseek": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "openrouter": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "openai_compatible": {{"temperature", "max_tokens", "top_p", "stop"}},
        "anthropic": {{"temperature", "max_tokens", "top_p", "top_k", "stop_sequences"}},
        "gemini": {{"temperature", "top_p", "top_k", "candidate_count", "max_output_tokens"}},
        "gemini_compatible": {{"temperature", "top_p", "top_k", "candidate_count", "max_output_tokens"}},
        "ollama": {{"temperature", "top_p", "top_k", "num_predict", "stop"}},
    }}
    options = {{key: values[key] for key in source_to_model[provider] if key in values}}
    if provider in {{"gemini", "gemini_compatible"}} and "max_tokens" in values:
        options.setdefault("max_output_tokens", values["max_tokens"])
    if provider == "ollama" and "max_tokens" in values:
        options.setdefault("num_predict", values["max_tokens"])
    return options
'''.replace("{body_line}", f"payload = {body_code}")
    if api_key_header:
        source = source.replace(
            f"api_key = config.{prefix}_API_KEY",
            f"api_key = headers.get({api_key_header!r}) or config.{prefix}_API_KEY",
        )
    return source, config_block, references


def _provider_for_model(node: dict[str, Any], model_config: dict[str, Any]) -> str:
    """Выбрать адаптер chat-модели по endpoint или типу узла n8n."""
    node_type = str(node.get("type", "")).casefold()
    url = str(model_config.get("api_url", ""))
    for provider in ("openai", "deepseek", "openrouter", "anthropic", "gemini", "ollama"):
        if provider in node_type:
            return provider
    if "claude" in node_type:
        return "anthropic"
    if "google" in node_type or "gemini" in node_type:
        return "gemini"
    if url:
        from n8n_transcriper.langgraph_mode.providers import match_provider_endpoint

        endpoint = match_provider_endpoint(url)
        if endpoint is not None:
            return endpoint.provider
    return "openai_compatible"


def generate_n8n_agent_node(
    agent_node: dict[str, Any],
    model_node: dict[str, Any],
    module_name: str,
    config_prefix: str,
) -> tuple[str, set[str], str]:
    """Сгенерировать AI Agent n8n с подключённой к нему моделью."""
    _, references, model_config = generate_ai_agent(
        agent_node, model_node, "run_node", config_prefix
    )
    provider = _provider_for_model(model_node, model_config)
    if provider not in _MODEL_PACKAGES:
        raise WorkflowError(f"Узел {agent_node.get('name')}: провайдер модели не поддерживается LangGraph")
    parameters = agent_node.get("parameters", {})
    agent_options = parameters.get("options", {})
    prompt = compile_langgraph_expression(
        parameters["text"], node=str(agent_node.get("name", "AI Agent")), parameter="text"
    )
    system = compile_langgraph_expression(
        agent_options.get("systemMessage", ""),
        node=str(agent_node.get("name", "AI Agent")),
        parameter="options.systemMessage",
    )
    references.update(prompt.dependencies)
    references.update(system.dependencies)
    module, _ = _MODEL_CLASSES[provider]
    constructor = _model_constructor(
        provider,
        model=f"config.{config_prefix}_MODEL",
        base_url=f"_base_url(config.{config_prefix}_API_URL, {provider!r})",
        key=f"config.{config_prefix}_API_KEY",
        options=f"_model_options(config.{config_prefix}_OPTIONS, {provider!r})",
    )
    source = f'''"""Агент LangGraph, созданный из узла n8n AI Agent {agent_node.get('name', '')!r}."""

import config
import re
from langchain.agents import create_agent
from {module} import {_MODEL_CLASSES[provider][1]}


def _base_url(api_url, provider):
    """Преобразовать сохранённый URL вызова в базовый адрес провайдера."""
    if not api_url:
        return {{
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "anthropic": "https://api.anthropic.com",
            "ollama": "http://localhost:11434",
        }}.get(provider, "")
    if provider == "anthropic":
        base_url = re.sub(r"/v1/messages$", "", api_url, flags=re.IGNORECASE)
    elif provider in {"gemini", "gemini_compatible"}:
        base_url = re.sub(
            r"/models/[^/:]+:(?:generateContent|streamGenerateContent)$",
            "",
            api_url,
            flags=re.IGNORECASE,
        )
        base_url = re.sub(r"/(?:v1/)?interactions$", "", base_url, flags=re.IGNORECASE)
    elif provider == "ollama":
        base_url = re.sub(r"/api/(?:chat|generate)$", "", api_url, flags=re.IGNORECASE)
    else:
        base_url = api_url.removesuffix("/chat/completions")
    base_url = base_url.rstrip("/")
    if not base_url:
        raise ValueError("Заполните API_URL модели в config.py")
    return base_url


def _model_options(values, provider):
    """Оставить только настройки, которые принимает выбранный адаптер модели."""
    allowed = {{
        "openai": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "deepseek": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "openrouter": {{"temperature", "max_tokens", "top_p", "stop", "frequency_penalty", "presence_penalty"}},
        "openai_compatible": {{"temperature", "max_tokens", "top_p", "stop"}},
        "anthropic": {{"temperature", "max_tokens", "top_p", "top_k", "stop_sequences"}},
        "gemini": {{"temperature", "max_output_tokens", "top_p", "top_k", "candidate_count"}},
        "gemini_compatible": {{"temperature", "max_output_tokens", "top_p", "top_k", "candidate_count"}},
        "ollama": {{"temperature", "num_predict", "top_p", "top_k", "stop"}},
    }}
    options = {{key: value for key, value in values.items() if key in allowed[provider]}}
    if provider in {{"gemini", "gemini_compatible"}} and "max_tokens" in values:
        options.setdefault("max_output_tokens", values["max_tokens"])
    if provider == "ollama" and "max_tokens" in values:
        options.setdefault("num_predict", values["max_tokens"])
    return options


{_message_helpers()}


async def run_node(items, results, execution):
    """Выполнить prompt n8n через скомпилированный агент LangGraph."""
    api_key = config.{config_prefix}_API_KEY
    if {provider!r} != "ollama" and not api_key:
        raise ValueError("Заполните {config_prefix}_API_KEY в config.py или переменную окружения")
    headers = {{}}
    header_name = config.{config_prefix}_API_KEY_HEADER
    header_prefix = config.{config_prefix}_API_KEY_PREFIX
    if header_name and (header_name.casefold() != "authorization" or header_prefix != "Bearer "):
        headers[header_name] = f"{{header_prefix}}{{api_key}}"
    model = {constructor}
    agent = create_agent(model=model, tools=[])
    output = []
    for item in items:
        messages = []
        system_text = {system.code}
        if system_text:
            messages.append({{"role": "system", "content": system_text}})
        messages.append({{"role": "user", "content": {prompt.code}}})
        result = await agent.ainvoke({{"messages": messages}})
        returned_messages = result.get("messages", [])
        if not returned_messages:
            raise ValueError("Агент LangGraph не вернул ответ модели")
        output.append({{"json": {{"output": _message_text(returned_messages[-1])}}}})
    return output
'''
    return source, references, _MODEL_PACKAGES[provider]
