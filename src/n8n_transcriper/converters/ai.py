"""Генерация прямых HTTP-вызовов для AI Agent с подключённой chat-моделью."""

import keyword
import re
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


_ENDPOINT_KEYS = ("apiUrl", "apiURL", "api_url", "endpoint", "url")
_BASE_URL_KEYS = ("baseUrl", "baseURL", "base_url")
_INTERNAL_OPTIONS = {
    "timeout", "maxRetries", "apiUrl", "apiURL", "api_url", "endpoint", "url",
    "baseUrl", "baseURL", "base_url", "apiKey",
    "api_key", "apiKeyHeader", "api_key_header", "apiKeyPrefix",
    "api_key_prefix", "stream", "streaming",
}
_OPTION_ALIASES = {"maxTokens": "max_tokens"}
_OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


def _completion_url(model: dict[str, Any]) -> str:
    """Получить URL Chat Completions из параметров модели или стандарт OpenRouter."""
    parameters = model.get("parameters", {})
    options = parameters.get("options", {})
    for source, keys, is_base_url in (
        (parameters, _ENDPOINT_KEYS, False),
        (options, _ENDPOINT_KEYS, False),
        (parameters, _BASE_URL_KEYS, True),
        (options, _BASE_URL_KEYS, True),
    ):
        for key in keys:
            value = source.get(key) if isinstance(source, dict) else None
            if not isinstance(value, str) or not value.strip():
                continue
            url = value.strip().rstrip("/")
            if url.endswith("/chat/completions"):
                return url
            if is_base_url or url.endswith("/v1"):
                return url + "/chat/completions"
            return url

    if "openrouter" in str(model.get("type", "")).lower():
        return _OPENROUTER_CHAT_COMPLETIONS_URL
    return ""


def _payload_options(options: dict[str, Any], node_name: str) -> dict[str, Any]:
    """Подготовить обычные параметры chat completion без списка провайдеров."""
    if options.get("stream", options.get("streaming", False)):
        raise WorkflowError(f"Узел {node_name}: потоковый ответ модели пока не поддерживается")
    payload = {}
    for key, value in options.items():
        if key in _INTERNAL_OPTIONS:
            continue
        api_key = _OPTION_ALIASES.get(key)
        if api_key is None:
            api_key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        payload[api_key] = value
    return payload


def generate_ai_agent(
    agent: dict[str, Any],
    model: dict[str, Any],
    function_name: str,
    config_prefix: str,
) -> tuple[str, set[str], dict[str, Any]]:
    """Сгенерировать обычный HTTP-вызов chat-модели и настройки для config.py.

    Конвертер не зависит от типа конкретного провайдера. Он использует endpoint
    из узла модели, а для моделей OpenAI-совместимого протокола без endpoint
    оставляет URL и credentials в конфигурации проекта.
    """
    name = agent.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    if not config_prefix.isidentifier() or keyword.iskeyword(config_prefix):
        raise WorkflowError(f"Узел {name}: некорректный префикс AI-конфигурации")

    parameters = agent.get("parameters", {})
    options = parameters.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: options AI Agent должен быть объектом")
    if options.get("enableStreaming", False):
        raise WorkflowError(f"Узел {name}: потоковый ответ AI Agent пока не поддерживается")
    prompt_value = parameters.get("text")
    if not isinstance(prompt_value, str) or not prompt_value:
        raise WorkflowError(f"Узел {name}: требуется текст prompt в параметре text")
    system_value = options.get("systemMessage", "")
    if not isinstance(system_value, str):
        raise WorkflowError(f"Узел {name}: options.systemMessage должен быть строкой")

    model_parameters = model.get("parameters", {})
    model_options = model_parameters.get("options", {})
    if not isinstance(model_options, dict):
        raise WorkflowError(f"Узел {model.get('name', '<без имени>')}: options модели должен быть объектом")
    model_id = model_parameters.get("model", "")
    if not isinstance(model_id, str):
        model_id = str(model_id) if model_id is not None else ""

    timeout_ms = model_options.get("timeout", 180000)
    try:
        timeout_seconds = float(timeout_ms) / 1000
    except (TypeError, ValueError):
        raise WorkflowError(f"Узел {model.get('name', '<без имени>')}: timeout модели должен быть числом") from None
    if timeout_seconds <= 0:
        raise WorkflowError(f"Узел {model.get('name', '<без имени>')}: timeout модели должен быть больше нуля")

    retries = model_options.get("maxRetries", 0)
    if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
        raise WorkflowError(f"Узел {model.get('name', '<без имени>')}: maxRetries модели должен быть целым числом не меньше нуля")

    payload_options = _payload_options(model_options, str(model.get("name", "AI model")))
    model_config = {
        "prefix": config_prefix,
        "node_name": model.get("name", "AI model"),
        "api_url": _completion_url(model),
        "api_key_header": str(model_parameters.get(
            "apiKeyHeader", model_options.get("apiKeyHeader", "Authorization")
        )),
        "api_key_prefix": str(model_parameters.get(
            "apiKeyPrefix", model_options.get("apiKeyPrefix", "Bearer ")
        )),
        "model": model_id,
        "options": payload_options,
        "timeout": timeout_seconds,
        "max_retries": retries,
    }

    prompt = compile_expression(prompt_value, node=name, parameter="text")
    system = compile_expression(system_value, node=name, parameter="options.systemMessage")
    prefix = config_prefix
    source = f'''import json
import time

import httpx
import config


def {function_name}(items, results, execution):
    """Вызвать подключённую chat-модель и вернуть ответы как n8n items."""
    if not config.{prefix}_API_URL:
        raise ValueError("Не задан {prefix}_API_URL в config.py")
    if not config.{prefix}_API_KEY:
        raise ValueError("Не задан {prefix}_API_KEY в config.py или окружении")
    if not config.{prefix}_MODEL:
        raise ValueError("Не задан {prefix}_MODEL в config.py")
    output = []
    for item in items:
        payload = {{
            **config.{prefix}_OPTIONS,
            "model": config.{prefix}_MODEL,
            "messages": [
                {{"role": "system", "content": {system.code}}},
                {{"role": "user", "content": {prompt.code}}},
            ],
        }}
        headers = {{"Content-Type": "application/json"}}
        headers[config.{prefix}_API_KEY_HEADER] = f"{{config.{prefix}_API_KEY_PREFIX}}{{config.{prefix}_API_KEY}}"
        for attempt in range(config.{prefix}_MAX_RETRIES + 1):
            try:
                response = httpx.post(
                    config.{prefix}_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=config.{prefix}_TIMEOUT,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt >= config.{prefix}_MAX_RETRIES:
                    raise
                time.sleep(min(2 ** attempt, 8))
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Ответ AI API не содержит choices[0].message.content") from exc
        output.append({{"json": {{"output": content}}}})
    return output
'''
    return source, set(prompt.dependencies | system.dependencies), model_config
