"""Tests for opt-in detection of LLM-style HTTP requests."""

from __future__ import annotations

import pytest

from n8n_transcriper.langgraph_mode.detection import detect_ai_request
from n8n_transcriper.langgraph_mode.providers import (
    endpoint_base_url,
    match_provider_endpoint,
)


@pytest.mark.parametrize(
    ("url", "provider", "operation"),
    [
        ("https://api.openai.com/v1/chat/completions", "openai", "chat_completion"),
        ("https://api.deepseek.com/v1/chat/completions", "deepseek", "chat_completion"),
        ("https://openrouter.ai/api/v1/chat/completions", "openrouter", "chat_completion"),
        ("https://api.anthropic.com/v1/messages", "anthropic", "messages"),
        (
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            "gemini",
            "generate_content",
        ),
        (
            "https://generativelanguage.googleapis.com/v1/interactions",
            "gemini",
            "interactions",
        ),
        ("http://localhost:11434/api/chat", "ollama", "chat"),
        ("http://localhost:11434/api/generate", "ollama", "generate"),
    ],
)
def test_known_provider_endpoint_requires_matching_host_and_route(url, provider, operation):
    endpoint = match_provider_endpoint(url)

    assert endpoint is not None
    assert endpoint.provider == provider
    assert endpoint.operation == operation


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
        (
            "https://api.openai.com/proxy/v1/chat/completions",
            "https://api.openai.com/proxy/v1",
        ),
        (
            "https://api.anthropic.com/v1/messages",
            "https://api.anthropic.com/v1",
        ),
    ],
)
def test_endpoint_base_url_preserves_api_prefix(url, expected):
    endpoint = match_provider_endpoint(url)

    assert endpoint is not None
    assert endpoint_base_url(url, endpoint) == expected


def _request(url: str, body: object, *, method: str = "POST") -> dict:
    return {
        "name": "HTTP Request",
        "parameters": {"method": method, "url": url, "jsonBody": body},
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/chat/completions",
        "https://api.deepseek.com/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
    ],
)
def test_known_chat_endpoint_and_chat_body_are_detected(url):
    result = detect_ai_request(
        _request(
            url,
            {
                "model": "provider-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.2,
            },
        )
    )

    assert result.is_ai is True
    assert result.confidence == "high"
    assert "body:messages-with-chat-roles" in result.evidence


def test_known_gemini_endpoint_uses_contents_shape():
    result = detect_ai_request(
        _request(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            {"contents": [{"parts": [{"text": "Summarize this"}]}]},
        )
    )

    assert (result.is_ai, result.provider, result.operation) == (
        True,
        "gemini",
        "generate_content",
    )


def test_custom_url_uses_full_body_signature_instead_of_domain():
    result = detect_ai_request(
        _request(
            "https://internal.company.by/api/run",
            {
                "model": "private-model",
                "messages": [{"role": "system", "content": "Helpful"}],
                "temperature": 0.7,
                "max_tokens": 2000,
            },
        )
    )

    assert result.is_ai is True
    assert result.provider == "openai_compatible"
    assert "body:openai-compatible-signature" in result.evidence


def test_messages_alone_do_not_classify_slack_or_telegram_as_ai():
    result = detect_ai_request(
        _request(
            "https://slack.example/api/chat.postMessage",
            {"channel": "C123", "messages": [{"role": "user", "content": "Hi"}]},
        )
    )

    assert result.is_ai is False
    assert result.provider is None


def test_known_provider_route_without_llm_body_is_only_a_candidate():
    result = detect_ai_request(
        _request(
            "https://api.openai.com/v1/chat/completions",
            {"messages": [{"role": "user", "content": "Hello"}]},
        )
    )

    assert result.is_ai is False
    assert result.confidence == "candidate"
    assert result.provider == "openai"


def test_generation_routes_alone_do_not_classify_unknown_api_as_ai():
    result = detect_ai_request(
        _request("https://internal.example/generate", {"prompt": "do it"})
    )

    assert result.is_ai is False


def test_expression_json_body_is_inspected_without_evaluating_dynamic_values():
    result = detect_ai_request(
        _request(
            "https://internal.company.by/api/run",
            "={{ {\n"
            "  'model': 'local-model',\n"
            "  'messages': [{'role': 'user', 'content': $json.prompt}],\n"
            "  'max_tokens': 400\n"
            "} }}",
        )
    )

    assert result.is_ai is True
    assert result.provider == "openai_compatible"
    assert "messages" in result.body_fields


def test_only_post_http_nodes_are_considered_for_model_calls():
    result = detect_ai_request(
        _request(
            "https://api.openai.com/v1/chat/completions",
            {
                "model": "provider-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.2,
            },
            method="GET",
        )
    )

    assert result.is_ai is False
