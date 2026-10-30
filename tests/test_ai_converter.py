"""Проверки генерации AI Agent и HTTP-вызовов chat-моделей.

Проверяются настройки модели, prompt и работа generated-кода без внешнего API.
"""

import sys
from types import SimpleNamespace

from n8n_transcriper.converters.ai import generate_ai_agent
from n8n_transcriper.generator import generate_project


def model_node():
    """Сформировать test-модель OpenRouter с фиксированными параметрами."""
    return {
        "id": "model-1", "name": "OpenRouter Chat Model",
        "type": "@n8n/n8n-nodes-langchain.lmChatOpenRouter",
        "parameters": {"model": "openai/gpt-4.1-mini", "options": {
            "temperature": 0.8, "maxTokens": 2500, "timeout": 180000, "maxRetries": 0,
        }},
    }


def agent_node():
    """Создать AI Agent, ссылающийся на модель и два prompt-шаблона."""
    return {
        "id": "agent-1", "name": "AI Agent - Analyze CSV",
        "type": "@n8n/n8n-nodes-langchain.agent",
        "parameters": {"promptType": "define",
                       "text": "={{ JSON.stringify($('Python - Build CSV').first().json.summary) + $json.csv_text }}",
                       "options": {"systemMessage": "Analyze the CSV."}},
    }


def test_openrouter_agent_prompt_and_settings(monkeypatch):
    agent = agent_node()
    model = model_node()
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "отчёт"}}]})

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, HTTPError=Exception))
    prefix = "OPENROUTER_CHAT_MODEL"
    config = SimpleNamespace(**{
        f"{prefix}_API_URL": "https://openrouter.ai/api/v1/chat/completions",
        f"{prefix}_API_KEY": "test-key",
        f"{prefix}_API_KEY_HEADER": "Authorization",
        f"{prefix}_API_KEY_PREFIX": "Bearer ",
        f"{prefix}_MODEL": "openai/gpt-4.1-mini",
        f"{prefix}_OPTIONS": {"temperature": 0.8, "max_tokens": 2500},
        f"{prefix}_TIMEOUT": 180,
        f"{prefix}_MAX_RETRIES": 0,
    })
    monkeypatch.setitem(sys.modules, "config", config)
    source, refs, model_config = generate_ai_agent(agent, model, "run", prefix)
    namespace = {}
    exec(source, namespace)
    result = namespace["run"]([{"json": {"csv_text": "a;b"}}],
        {"Python - Build CSV": [{"json": {"summary": {"total": 2}}}]}, {})
    assert refs == {"Python - Build CSV"}
    assert result == [{"json": {"output": "отчёт"}}]
    request = calls[0][1]
    assert calls[0][0] == "https://openrouter.ai/api/v1/chat/completions"
    assert request["json"]["model"] == "openai/gpt-4.1-mini"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["json"]["temperature"] == 0.8
    assert request["json"]["max_tokens"] == 2500
    assert request["timeout"] == 180
    assert '"total":2' in request["json"]["messages"][1]["content"]
    assert "a;b" in request["json"]["messages"][1]["content"]
    assert model_config["api_url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_generic_model_endpoint_and_options_are_preserved():
    agent = {"name": "Agent", "parameters": {"promptType": "define", "text": "Hi", "options": {"systemMessage": "System"}}}
    model = {"type": "custom.provider.chatModel", "parameters": {
        "model": "custom-model", "baseURL": "https://llm.example/v1",
        "options": {"temperature": 0.1, "responseFormat": {"type": "json_object"}, "vendorOption": True},
    }}
    _, _, settings = generate_ai_agent(agent, model, "run", "CUSTOM_MODEL")
    assert settings["api_url"] == "https://llm.example/v1/chat/completions"
    assert settings["options"] == {
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "vendor_option": True,
    }


def test_generator_connects_model_without_executing_it(tmp_path):
    agent = agent_node()
    agent["parameters"] = {**agent["parameters"], "text": "={{ $json.csv_text }}"}
    model = model_node()
    workflow = {"nodes": [agent, model], "connections": {
        model["name"]: {"ai_languageModel": [[{"node": agent["name"], "type": "ai_languageModel", "index": 0}]]}}}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    config = (output / "config.py").read_text(encoding="utf-8")
    assert 'OPENROUTER_CHAT_MODEL_API_KEY' in config
    assert 'https://openrouter.ai/api/v1/chat/completions' in config
    assert len(list((output / "ai_nodes").glob("ai_agent_analyze_csv.py"))) == 1
    assert "config.OPENROUTER_CHAT_MODEL_API_URL" in (output / "ai_nodes" / "ai_agent_analyze_csv.py").read_text(encoding="utf-8")


def test_generator_accepts_unrecognized_provider_with_node_endpoint(tmp_path):
    agent = {
        "id": "agent-custom", "name": "Custom AI Agent",
        "type": "@n8n/n8n-nodes-langchain.agent",
        "parameters": {"promptType": "define", "text": "={{ $json.prompt }}", "options": {}},
    }
    model = {
        "id": "model-custom", "name": "Vendor Chat Model",
        "type": "vendor.nodes.chatModel",
        "parameters": {
            "model": "vendor-model-v2",
            "baseURL": "https://llm.vendor.example/v1",
            "options": {"temperature": 0.4, "vendorSetting": "kept"},
        },
    }
    workflow = {"nodes": [agent, model], "connections": {
        model["name"]: {"ai_languageModel": [[{
            "node": agent["name"], "type": "ai_languageModel", "index": 0,
        }]]},
    }}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    config = (output / "config.py").read_text(encoding="utf-8")
    source = (output / "ai_nodes" / "custom_ai_agent.py").read_text(encoding="utf-8")
    assert "VENDOR_CHAT_MODEL_API_URL" in config
    assert "https://llm.vendor.example/v1/chat/completions" in config
    assert "vendor_setting" in config
    assert "config.VENDOR_CHAT_MODEL_API_URL" in source
