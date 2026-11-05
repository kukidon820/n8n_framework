"""Адреса AI-провайдеров, распознаваемые в режиме LangGraph."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderEndpoint:
    """Описать API-адрес провайдера, для которого доступен адаптер chat-модели."""

    provider: str
    operation: str
    hosts: tuple[str, ...]
    path_suffixes: tuple[str, ...]
    default_base_path: str = ""


# Храним адреса провайдеров в одном месте, чтобы добавление нового провайдера
# не размножало проверки host и endpoint по генератору workflow.
PROVIDER_ENDPOINTS: tuple[ProviderEndpoint, ...] = (
    ProviderEndpoint(
        provider="openai",
        operation="chat_completion",
        hosts=("api.openai.com",),
        path_suffixes=("/v1/chat/completions",),
        default_base_path="/v1",
    ),
    ProviderEndpoint(
        provider="deepseek",
        operation="chat_completion",
        hosts=("api.deepseek.com",),
        path_suffixes=("/v1/chat/completions", "/chat/completions"),
        default_base_path="/v1",
    ),
    ProviderEndpoint(
        provider="openrouter",
        operation="chat_completion",
        hosts=("openrouter.ai", "api.openrouter.ai"),
        path_suffixes=("/api/v1/chat/completions",),
        default_base_path="/api/v1",
    ),
    ProviderEndpoint(
        provider="anthropic",
        operation="messages",
        hosts=("api.anthropic.com",),
        path_suffixes=("/v1/messages",),
        default_base_path="/v1",
    ),
    ProviderEndpoint(
        provider="gemini",
        operation="generate_content",
        hosts=("generativelanguage.googleapis.com",),
        path_suffixes=(":generatecontent", ":streamgeneratecontent"),
        default_base_path="",
    ),
    ProviderEndpoint(
        provider="gemini",
        operation="interactions",
        hosts=("generativelanguage.googleapis.com",),
        path_suffixes=("/v1/interactions", "/interactions"),
        default_base_path="/v1",
    ),
    ProviderEndpoint(
        provider="ollama",
        operation="chat",
        hosts=("localhost", "127.0.0.1", "0.0.0.0", "ollama"),
        path_suffixes=("/api/chat",),
        default_base_path="",
    ),
    ProviderEndpoint(
        provider="ollama",
        operation="generate",
        hosts=("localhost", "127.0.0.1", "0.0.0.0", "ollama"),
        path_suffixes=("/api/generate",),
        default_base_path="",
    ),
)


def match_provider_endpoint(url: str) -> ProviderEndpoint | None:
    """Найти endpoint только при совпадении host и пути запроса.

    Совпадения одного пути недостаточно: метод Telegram Bot API или внутренний
    API может иметь похожее имя пути, не обращаясь к провайдеру языковой модели.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold().rstrip("/")
        port = parsed.port
    except ValueError:
        return None

    for endpoint in PROVIDER_ENDPOINTS:
        host_matches = host in endpoint.hosts
        # Ollama часто доступен по локальному имени узла, но сохраняет
        # стандартный порт 11434.
        if endpoint.provider == "ollama" and port == 11434:
            host_matches = True
        if not host_matches:
            continue
        if any(path.endswith(suffix) for suffix in endpoint.path_suffixes):
            return endpoint
    return None


def endpoint_base_url(url: str, endpoint: ProviderEndpoint) -> str:
    """Убрать путь операции, сохранив префикс обратного прокси."""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    lower_path = path.casefold()
    suffix = next(
        (suffix for suffix in endpoint.path_suffixes if lower_path.endswith(suffix)),
        "",
    )
    prefix = path[: len(path) - len(suffix)] if suffix else path
    base_path = endpoint.default_base_path
    if endpoint.provider in {"openai", "deepseek", "openrouter", "anthropic"}:
        # Сохраняем версию API из исходного URL и префикс шлюза,
        # который может стоять перед известным путём.
        if prefix.endswith(base_path):
            prefix = prefix[: -len(base_path)] if base_path else prefix
        elif endpoint.provider == "deepseek" and prefix.endswith("/v1"):
            prefix = prefix[:-3]
        return f"{parsed.scheme}://{parsed.netloc}{prefix}{base_path}"
    return f"{parsed.scheme}://{parsed.netloc}{prefix}"
