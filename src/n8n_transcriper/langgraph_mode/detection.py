"""Осторожное распознавание AI-вызовов среди узлов HTTP Request.

Модуль вызывается только при явном флаге ``--langgraph``. Он сопоставляет адрес
провайдера со структурой JSON-тела; одного списка сообщений недостаточно,
чтобы признать запрос обращением к языковой модели.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any

from n8n_transcriper.langgraph_mode.expressions import compile_langgraph_expression
from n8n_transcriper.langgraph_mode.providers import (
    ProviderEndpoint,
    match_provider_endpoint,
)
from n8n_transcriper.reader import WorkflowError


_UNKNOWN = object()
_CHAT_ROLES = frozenset({"system", "developer", "user", "assistant", "model"})
_GENERATION_FIELDS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "maxoutputtokens",
        "candidate_count",
        "candidatecount",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "stream",
    }
)


@dataclass(frozen=True)
class AiRequestDetection:
    """Результат определения провайдера и протокола с данными для диагностики."""

    is_ai: bool
    provider: str | None
    operation: str | None
    confidence: str
    evidence: tuple[str, ...]
    endpoint: ProviderEndpoint | None = None
    body_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class _BodyShape:
    fields: frozenset[str]
    has_role_messages: bool
    has_text_contents: bool
    has_prompt: bool
    has_generation_options: bool


def _static_value(node: ast.AST) -> Any:
    """Прочитать только структуру литералов; не исполнять expression."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_static_value(value) for value in node.elts]
        if any(value is _UNKNOWN for value in values):
            return _UNKNOWN
        return values
    if isinstance(node, ast.Dict):
        result: dict[str, Any] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            key = _static_value(key_node) if key_node is not None else _UNKNOWN
            if not isinstance(key, str):
                continue
            result[key] = _static_value(value_node)
        return result
    return _UNKNOWN


def _expression_body(value: str, node_name: str) -> Any:
    """Разобрать литеральное тело или его структуру, не исполняя JavaScript."""
    try:
        parsed_json = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed_json = _UNKNOWN
    if isinstance(parsed_json, dict):
        return parsed_json

    try:
        expression = compile_langgraph_expression(
            value, node=node_name, parameter="jsonBody"
        )
        tree = ast.parse(expression.code, mode="eval")
    except (SyntaxError, ValueError, TypeError, WorkflowError):
        return _UNKNOWN
    return _static_value(tree.body)


def _http_body(parameters: dict[str, Any], node_name: str) -> Any:
    """Извлечь структуру тела из JSON, пар ключ/значение или raw body n8n."""
    if parameters.get("contentType", "json") == "json":
        json_body = parameters.get("jsonBody")
        if isinstance(json_body, dict):
            return json_body
        if isinstance(json_body, str):
            return _expression_body(json_body, node_name)

    body_parameters = parameters.get("bodyParameters")
    if isinstance(body_parameters, dict):
        pairs = body_parameters.get("parameters")
        if isinstance(pairs, list):
            body: dict[str, Any] = {}
            for pair in pairs:
                if not isinstance(pair, dict):
                    return _UNKNOWN
                key = pair.get("name")
                value = pair.get("value", _UNKNOWN)
                if isinstance(key, str):
                    body[key] = value
            return body

    raw_body = parameters.get("body")
    if isinstance(raw_body, dict):
        return raw_body
    if isinstance(raw_body, str):
        return _expression_body(raw_body, node_name)
    return _UNKNOWN


def _has_role_messages(body: dict[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    return any(
        isinstance(message, dict)
        and message.get("role") in _CHAT_ROLES
        and "content" in message
        for message in messages
    )


def _has_text_contents(body: dict[str, Any]) -> bool:
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents:
        return False
    for content in contents:
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if isinstance(parts, list) and any(
            isinstance(part, dict) and "text" in part
            for part in parts
        ):
            return True
    return False


def _body_shape(body: Any) -> _BodyShape:
    if not isinstance(body, dict):
        return _BodyShape(frozenset(), False, False, False, False)
    fields = frozenset(key.casefold() for key in body)
    has_options = bool(fields & _GENERATION_FIELDS)
    generation_config = body.get("generationConfig")
    if isinstance(generation_config, dict):
        has_options = has_options or bool(
            {key.casefold() for key in generation_config} & _GENERATION_FIELDS
        )
    return _BodyShape(
        fields=fields,
        has_role_messages=_has_role_messages(body),
        has_text_contents=_has_text_contents(body),
        has_prompt="prompt" in body,
        has_generation_options=has_options,
    )


def _body_evidence(shape: _BodyShape) -> tuple[str, ...]:
    evidence: list[str] = []
    if "model" in shape.fields:
        evidence.append("body:model")
    if shape.has_role_messages:
        evidence.append("body:messages-with-chat-roles")
    if shape.has_text_contents:
        evidence.append("body:contents-with-text-parts")
    if shape.has_prompt:
        evidence.append("body:prompt")
    if shape.has_generation_options:
        evidence.append("body:generation-options")
    return tuple(evidence)


def detect_ai_request(node: dict[str, Any]) -> AiRequestDetection:
    """Распознать HTTP Request как AI-вызов только при достаточных признаках.

    Для известного endpoint требуется совместимая структура тела. Для
    неизвестного адреса нужна более строгая сигнатура: модель и сообщения
    с ролями либо prompt для генерации текста, а также хотя бы один параметр
    генерации. Это не позволяет принять Slack, Telegram и похожие API за
    вызов модели только из-за наличия массива сообщений.
    """
    parameters = node.get("parameters", {})
    if not isinstance(parameters, dict):
        return AiRequestDetection(False, None, None, "none", ())
    method = parameters.get("method", "GET")
    if not isinstance(method, str) or method.upper() != "POST":
        return AiRequestDetection(False, None, None, "none", ())
    if parameters.get("sendBody", True) is False:
        return AiRequestDetection(False, None, None, "none", ())
    if parameters.get("contentType", "json") not in {"json", "application/json"}:
        return AiRequestDetection(False, None, None, "none", ())
    url = parameters.get("url")
    if not isinstance(url, str):
        return AiRequestDetection(False, None, None, "none", ())

    endpoint = match_provider_endpoint(url)
    body = _http_body(parameters, str(node.get("name") or "HTTP Request"))
    shape = _body_shape(body)
    evidence = list(_body_evidence(shape))
    if endpoint is not None:
        evidence.insert(0, "url:provider-host-and-operation")

    model_present = "model" in shape.fields
    chat_shape = model_present and shape.has_role_messages
    gemini_shape = shape.has_text_contents and shape.has_generation_options
    prompt_shape = model_present and shape.has_prompt and shape.has_generation_options

    if endpoint is not None:
        if endpoint.operation in {"chat_completion", "messages", "chat"}:
            compatible_body = chat_shape
        elif endpoint.operation == "generate_content":
            compatible_body = shape.has_text_contents or chat_shape
        elif endpoint.operation == "interactions":
            compatible_body = (
                ("agent" in shape.fields and "input" in shape.fields)
                or (
                    model_present
                    and (
                        "input" in shape.fields
                        or chat_shape
                        or shape.has_prompt
                        or shape.has_text_contents
                    )
                )
            )
        elif endpoint.operation == "generate":
            compatible_body = model_present and shape.has_prompt
        else:
            compatible_body = False
        if compatible_body:
            return AiRequestDetection(
                True,
                endpoint.provider,
                endpoint.operation,
                "high",
                tuple(evidence),
                endpoint,
                tuple(sorted(shape.fields)),
            )
        return AiRequestDetection(
            False,
            endpoint.provider,
            endpoint.operation,
            "candidate",
            tuple(evidence),
            endpoint,
            tuple(sorted(shape.fields)),
        )

    # Внутренний или собственный endpoint тоже может быть совместим с OpenAI.
    # Одного URL мало: требуем модель, сообщения с ролями и параметр генерации.
    if chat_shape and shape.has_generation_options:
        evidence.append("body:openai-compatible-signature")
        return AiRequestDetection(
            True,
            "openai_compatible",
            "chat_completion",
            "high",
            tuple(evidence),
            None,
            tuple(sorted(shape.fields)),
        )
    if gemini_shape:
        evidence.append("body:gemini-compatible-signature")
        return AiRequestDetection(
            True,
            "gemini_compatible",
            "generate_content",
            "medium",
            tuple(evidence),
            None,
            tuple(sorted(shape.fields)),
        )
    if prompt_shape:
        evidence.append("body:model-prompt-generation-signature")
        return AiRequestDetection(
            True,
            "openai_compatible",
            "completion",
            "medium",
            tuple(evidence),
            None,
            tuple(sorted(shape.fields)),
        )

    return AiRequestDetection(
        False,
        None,
        None,
        "candidate" if evidence else "none",
        tuple(evidence),
        None,
        tuple(sorted(shape.fields)),
    )
