"""Проверки агентов провайдеров, создаваемых в режиме LangGraph."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest

from n8n_transcriper.generator import generate_project


def _http_node(
    *,
    name: str = "LLM Request",
    url: str = "https://internal.example/api/run",
    body: str | dict | None = None,
    headers: list[dict[str, str]] | None = None,
) -> dict:
    parameters = {
        "method": "POST",
        "url": url,
        "sendBody": True,
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": body if isinstance(body, str) else json.dumps(
            body
            or {
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 0.2,
            }
        ),
    }
    if headers:
        parameters["sendHeaders"] = True
        parameters["headerParameters"] = {"parameters": headers}
    return {
        "id": name,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "parameters": parameters,
    }


def _load_generated_module(source: str, monkeypatch: pytest.MonkeyPatch):
    """Выполнить сгенерированный код с подменами config и API LangChain."""
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    class FakeAgent:
        async def ainvoke(self, state):
            captured["messages"] = state["messages"]
            return {"messages": [SimpleNamespace(content="generated answer")]}

    def create_agent(*, model, tools):
        captured["agent_model"] = model
        captured["tools"] = tools
        return FakeAgent()

    langchain = types.ModuleType("langchain")
    langchain.__path__ = []
    agents = types.ModuleType("langchain.agents")
    agents.create_agent = create_agent
    openai = types.ModuleType("langchain_openai")
    openai.ChatOpenAI = FakeChatOpenAI
    google_genai = types.ModuleType("langchain_google_genai")
    google_genai.ChatGoogleGenerativeAI = FakeChatOpenAI
    ollama = types.ModuleType("langchain_ollama")
    ollama.ChatOllama = FakeChatOpenAI
    config = types.ModuleType("config")
    config.LANGGRAPH_HTTP_LLM_REQUEST_BASE_URL = "https://internal.example/api"
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY = ""
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_HEADER = "Authorization"
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_PREFIX = "Bearer "
    monkeypatch.setitem(sys.modules, "langchain", langchain)
    monkeypatch.setitem(sys.modules, "langchain.agents", agents)
    monkeypatch.setitem(sys.modules, "langchain_openai", openai)
    monkeypatch.setitem(sys.modules, "langchain_google_genai", google_genai)
    monkeypatch.setitem(sys.modules, "langchain_ollama", ollama)
    monkeypatch.setitem(sys.modules, "config", config)
    module = types.ModuleType("generated_ai_node")
    monkeypatch.setitem(sys.modules, "generated_ai_node", module)
    exec(compile(source, "generated_ai_node.py", "exec"), module.__dict__)
    return module, captured


def test_default_mode_keeps_ai_http_node_as_ordinary_http_request():
    files = generate_project({"nodes": [_http_node()], "connections": {}})

    assert "http_requests/llm_request.py" in files
    assert "ai_nodes/llm_request.py" not in files
    assert "langgraph" not in files["requirements.txt"]
    assert "langchain" not in files["requirements.txt"]


def test_langgraph_mode_generates_provider_agent_from_openai_chat_body(
    monkeypatch,
):
    node = _http_node(
        url="https://api.openai.com/v1/chat/completions",
        body=(
            "={{ {model: 'private-model', messages: "
            "[{role: 'system', content: 'Be concise'}, "
            "{role: 'user', content: $json.prompt}], "
            "temperature: 0.7, max_tokens: 2000} }}"
        )
    )
    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)

    assert "ai_nodes/llm_request.py" in files
    assert "http_requests/llm_request.py" not in files
    assert "from ai_nodes.llm_request import run_node" in files["main.py"]
    assert "create_agent(model=model, tools=[])" in files["ai_nodes/llm_request.py"]
    assert "langchain>=1.0" in files["requirements.txt"]
    assert "langchain-openai" in files["requirements.txt"]
    assert "langgraph>=1.0" in files["requirements.txt"]
    assert "httpx>=0.27" not in files["requirements.txt"]
    assert "http_requests/__init__.py" not in files
    assert "OpenAI и DeepSeek `/v1/chat/completions`" in files["README.md"]

    module, captured = _load_generated_module(files["ai_nodes/llm_request.py"], monkeypatch)
    module.config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY = "test-key"
    result = asyncio.run(
        module.run_node(
            [{"json": {"prompt": "Summarize these rows"}}],
            {},
            {"id": "execution-1"},
        )
    )

    assert result == [
        {
            "json": {
                "choices": [
                    {"message": {"role": "assistant", "content": "generated answer"}}
                ],
                "model": "private-model",
            }
        }
    ]
    assert captured["messages"] == [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Summarize these rows"},
    ]
    model_kwargs = captured["model_kwargs"]
    assert isinstance(model_kwargs, dict)
    assert model_kwargs["api_key"] == "test-key"
    assert model_kwargs["model"] == "private-model"
    assert model_kwargs["temperature"] == 0.7
    assert model_kwargs["max_tokens"] == 2000
    assert "model_kwargs" not in model_kwargs


def test_internal_ai_endpoint_preserves_exact_url_and_request_options(
    monkeypatch,
):
    secret = "CUSTOM_ENDPOINT_API_KEY_MUST_NOT_LEAK"
    node = _http_node(
        url=(
            "https://internal.example/api/run?tenant=finance"
            f"&api_key={secret}"
        ),
        body={
            "model": "private-model",
            "messages": [
                {"role": "user", "content": "={{ $json.prompt }}"}
            ],
            "temperature": 0.4,
            "max_tokens": 900,
        },
    )
    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)
    source = files["ai_nodes/llm_request.py"]

    assert "class WorkflowEndpointChatModel" in source
    assert "endpoint=config.LANGGRAPH_HTTP_LLM_REQUEST_ENDPOINT_URL" in source
    assert "https://internal.example/api/run?tenant=finance" in files["config.py"]
    assert secret not in "\n".join(files.values())
    assert "langchain>=1.0" in files["requirements.txt"]
    assert "httpx>=0.27" in files["requirements.txt"]
    assert "langchain-openai" not in files["requirements.txt"]
    assert "choices[0].message.content" in files["README.md"]
    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")

    captured: dict[str, object] = {}

    class FakeMessage:
        def __init__(self, content, tool_call_id=None):
            self.content = content
            self.tool_call_id = tool_call_id

    class FakeAIMessage(FakeMessage):
        pass

    class FakeHumanMessage(FakeMessage):
        pass

    class FakeSystemMessage(FakeMessage):
        pass

    class FakeToolMessage(FakeMessage):
        pass

    class FakeBaseChatModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeChatGeneration:
        def __init__(self, message):
            self.message = message

    class FakeChatResult:
        def __init__(self, generations):
            self.generations = generations

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "custom answer"}}
                ]
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResponse()

    core = types.ModuleType("langchain_core")
    core.__path__ = []
    language_models = types.ModuleType("langchain_core.language_models")
    language_models.__path__ = []
    chat_models = types.ModuleType("langchain_core.language_models.chat_models")
    chat_models.BaseChatModel = FakeBaseChatModel
    messages_module = types.ModuleType("langchain_core.messages")
    messages_module.AIMessage = FakeAIMessage
    messages_module.BaseMessage = FakeMessage
    messages_module.HumanMessage = FakeHumanMessage
    messages_module.SystemMessage = FakeSystemMessage
    messages_module.ToolMessage = FakeToolMessage
    outputs = types.ModuleType("langchain_core.outputs")
    outputs.ChatGeneration = FakeChatGeneration
    outputs.ChatResult = FakeChatResult
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = FakeAsyncClient

    def create_agent(*, model, tools):
        assert tools == []

        class FakeAgent:
            async def ainvoke(self, state):
                messages = []
                for entry in state["messages"]:
                    message_class = {
                        "system": FakeSystemMessage,
                        "user": FakeHumanMessage,
                        "assistant": FakeAIMessage,
                        "tool": FakeToolMessage,
                    }[entry["role"]]
                    messages.append(
                        message_class(
                            entry["content"], entry.get("tool_call_id")
                        )
                    )
                generated = await model._agenerate(messages)
                return {
                    "messages": state["messages"]
                    + [generated.generations[0].message]
                }

        return FakeAgent()

    agents = types.ModuleType("langchain.agents")
    agents.create_agent = create_agent
    langchain = types.ModuleType("langchain")
    langchain.__path__ = []
    config = types.ModuleType("config")
    config.LANGGRAPH_HTTP_LLM_REQUEST_ENDPOINT_URL = (
        "https://internal.example/api/run?tenant=finance"
    )
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY = ""
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_HEADER = "Authorization"
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_PREFIX = "Bearer "
    for name, module in {
        "langchain": langchain,
        "langchain.agents": agents,
        "langchain_core": core,
        "langchain_core.language_models": language_models,
        "langchain_core.language_models.chat_models": chat_models,
        "langchain_core.messages": messages_module,
        "langchain_core.outputs": outputs,
        "httpx": httpx,
        "config": config,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    generated_module = types.ModuleType("generated_custom_ai_node")
    exec(compile(source, "generated_custom_ai_node.py", "exec"), generated_module.__dict__)
    result = asyncio.run(
        generated_module.run_node(
            [{"json": {"prompt": "Keep this exact endpoint"}}],
            {},
            {"id": "execution-custom"},
        )
    )

    assert captured["url"] == "https://internal.example/api/run?tenant=finance"
    assert captured["body"] == {
        "temperature": 0.4,
        "max_tokens": 900,
        "model": "private-model",
        "messages": [
            {"role": "user", "content": "Keep this exact endpoint"}
        ],
    }
    assert result == [
        {
            "json": {
                "choices": [
                    {"message": {"role": "assistant", "content": "custom answer"}}
                ],
                "model": "private-model",
            }
        }
    ]


def test_gemini_generation_config_and_ollama_options_reach_model_constructor(
    monkeypatch,
):
    gemini_files = generate_project(
        {
            "nodes": [
                _http_node(
                    url=(
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        "gemini-test:generateContent"
                    ),
                    body={
                        "contents": [
                            {"parts": [{"text": "Summarize this"}]}
                        ],
                        "generationConfig": {
                            "temperature": 0.35,
                            "maxOutputTokens": 640,
                            "candidateCount": 2,
                        },
                    },
                )
            ],
            "connections": {},
        },
        langgraph=True,
    )
    gemini_module, gemini_captured = _load_generated_module(
        gemini_files["ai_nodes/llm_request.py"], monkeypatch
    )
    gemini_module.config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY = "gemini-key"
    asyncio.run(
        gemini_module.run_node(
            [{"json": {}}], {}, {"id": "execution-gemini"}
        )
    )
    gemini_options = gemini_captured["model_kwargs"]
    assert isinstance(gemini_options, dict)
    assert gemini_options["temperature"] == 0.35
    assert gemini_options["max_output_tokens"] == 640
    assert gemini_options["candidate_count"] == 2

    ollama_files = generate_project(
        {
            "nodes": [
                _http_node(
                    url="http://localhost:11434/api/chat",
                    body={
                        "model": "local-test",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "options": {"temperature": 0.55, "num_predict": 240},
                    },
                )
            ],
            "connections": {},
        },
        langgraph=True,
    )
    ollama_module, ollama_captured = _load_generated_module(
        ollama_files["ai_nodes/llm_request.py"], monkeypatch
    )
    asyncio.run(
        ollama_module.run_node(
            [{"json": {}}], {}, {"id": "execution-ollama"}
        )
    )
    ollama_options = ollama_captured["model_kwargs"]
    assert isinstance(ollama_options, dict)
    assert ollama_options["temperature"] == 0.55
    assert ollama_options["num_predict"] == 240


def test_langgraph_mode_moves_static_http_secret_to_config_without_self_reference():
    secret = "DO_NOT_WRITE_THIS_SECRET"
    node = _http_node(
        headers=[{"name": "Authorization", "value": f"Bearer {secret}"}]
    )
    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)

    assert all(secret not in source for source in files.values())
    assert "HTTP_HEADER_AUTHORIZATION" in files["config.py"]
    namespace: dict[str, object] = {}
    exec(compile(files["config.py"], "config.py", "exec"), namespace)
    assert "LANGGRAPH_HTTP_LLM_REQUEST_API_KEY" in namespace


def test_gemini_model_id_is_taken_from_generate_content_url():
    node = _http_node(
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent?key=redacted"
        ),
        body=(
            "={{ {contents: [{parts: [{text: $json.prompt}]}], "
            "generationConfig: {temperature: 0.2, maxOutputTokens: 300}, "
            "systemInstruction: {parts: [{text: 'Be helpful'}]}} }}"
        ),
    )
    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)
    source = files["ai_nodes/llm_request.py"]

    assert "gemini-2.0-flash" in source
    assert "generativelanguage.googleapis.com/v1beta/models" not in files["config.py"]
    assert "systemInstruction" in source
    assert "redacted" not in "\n".join(files.values())
    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")


@pytest.mark.parametrize(
    ("url", "body", "model_import", "dependency"),
    [
        (
            "https://api.openai.com/v1/chat/completions",
            {"model": "gpt-test", "messages": [{"role": "user", "content": "Hi"}], "temperature": 0.1},
            "from langchain_openai import ChatOpenAI",
            "langchain-openai",
        ),
        (
            "https://api.deepseek.com/v1/chat/completions",
            {"model": "deepseek-test", "messages": [{"role": "user", "content": "Hi"}], "temperature": 0.1},
            "from langchain_openai import ChatOpenAI",
            "langchain-openai",
        ),
        (
            "https://openrouter.ai/api/v1/chat/completions",
            {"model": "openrouter-test", "messages": [{"role": "user", "content": "Hi"}], "temperature": 0.1},
            "from langchain_openai import ChatOpenAI",
            "langchain-openai",
        ),
        (
            "https://api.anthropic.com/v1/messages",
            {"model": "claude-test", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100},
            "from langchain_anthropic import ChatAnthropic",
            "langchain-anthropic",
        ),
        (
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
            {"contents": [{"parts": [{"text": "Hi"}]}], "generationConfig": {"temperature": 0.2}},
            "from langchain_google_genai import ChatGoogleGenerativeAI",
            "langchain-google-genai",
        ),
        (
            "http://localhost:11434/api/chat",
            {"model": "ollama-test", "messages": [{"role": "user", "content": "Hi"}]},
            "from langchain_ollama import ChatOllama",
            "langchain-ollama",
        ),
        (
            "http://localhost:11434/api/generate",
            {"model": "ollama-test", "prompt": "Hi", "temperature": 0.2},
            "from langchain_ollama import ChatOllama",
            "langchain-ollama",
        ),
    ],
)
def test_supported_provider_endpoints_generate_matching_chat_adapters(
    url, body, model_import, dependency
):
    files = generate_project(
        {"nodes": [_http_node(url=url, body=body)], "connections": {}},
        langgraph=True,
    )

    source = files["ai_nodes/llm_request.py"]
    assert model_import in source
    assert dependency in files["requirements.txt"]
    assert "create_agent(model=model, tools=[])" in source
    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")


def test_gemini_interactions_uses_async_genai_sdk_and_preserves_remote_agent_fields(
    monkeypatch,
):
    secret = "INTERACTIONS_SECRET_MUST_NOT_LEAK"
    node = _http_node(
        url=(
            "https://generativelanguage.googleapis.com/v1/interactions"
            f"?key={secret}"
        ),
        body={"agent": "research-agent", "input": "={{ $json.prompt }}", "store": False},
    )

    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)
    source = files["ai_nodes/llm_request.py"]

    assert "from google import genai" in source
    assert "async_client.interactions.create" in source
    assert "'agent':" in source
    assert "google-genai>=2.3.0" in files["requirements.txt"]
    assert "langchain-google-genai" not in files["requirements.txt"]
    assert all(secret not in content for content in files.values())
    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")

    captured: dict[str, object] = {}

    class FakeInteractions:
        async def create(self, **payload):
            captured["payload"] = payload
            return {"id": "interaction-1", "output_text": "Research result"}

    class FakeAsyncClient:
        interactions = FakeInteractions()

        async def aclose(self):
            captured["async_closed"] = True

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs
            self.aio = FakeAsyncClient()

        def close(self):
            captured["client_closed"] = True

    google = types.ModuleType("google")
    google.__path__ = []
    genai = types.ModuleType("google.genai")
    genai.Client = FakeClient
    genai.types = types.SimpleNamespace(
        HttpOptions=lambda **kwargs: SimpleNamespace(**kwargs)
    )
    genai_module = types.ModuleType("google.genai.types")
    genai_module.HttpOptions = genai.types.HttpOptions
    google.genai = genai
    config = types.ModuleType("config")
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY = "test-key"
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_HEADER = "x-goog-api-key"
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_KEY_PREFIX = ""
    config.LANGGRAPH_HTTP_LLM_REQUEST_API_VERSION = "v1"
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_module)
    monkeypatch.setitem(sys.modules, "config", config)
    module = types.ModuleType("generated_gemini_interactions")
    exec(compile(source, "generated_gemini_interactions.py", "exec"), module.__dict__)

    result = asyncio.run(
        module.run_node(
            [{"json": {"prompt": "Inspect these findings"}}],
            {},
            {"id": "execution-1"},
        )
    )

    assert result == [
        {"json": {"id": "interaction-1", "output_text": "Research result"}}
    ]
    assert captured["payload"] == {
        "agent": "research-agent",
        "input": "Inspect these findings",
        "store": False,
        "extra_headers": None,
    }
    assert captured["async_closed"] is True
    assert captured["client_closed"] is True


def test_messages_only_on_non_provider_http_endpoint_stays_regular_http():
    node = _http_node(
        url="https://slack.example/api/chat.postMessage",
        body={"channel": "C123", "messages": [{"role": "user", "content": "Hi"}]},
    )
    files = generate_project({"nodes": [node], "connections": {}}, langgraph=True)

    assert "http_requests/llm_request.py" in files
    assert "ai_nodes/llm_request.py" not in files
    assert "langchain-openai" not in files["requirements.txt"]


def test_n8n_ai_agent_uses_connected_provider_model_only_in_langgraph_mode():
    agent = {
        "id": "agent-id",
        "name": "AI Agent",
        "type": "@n8n/n8n-nodes-langchain.agent",
        "parameters": {
            "text": "={{ $json.prompt }}",
            "options": {"systemMessage": "Review the supplied data."},
        },
    }
    model = {
        "id": "model-id",
        "name": "OpenRouter Chat Model",
        "type": "@n8n/n8n-nodes-langchain.lmChatOpenRouter",
        "parameters": {
            "model": "deepseek/deepseek-chat",
            "options": {"temperature": 0.3, "maxTokens": 1200},
        },
    }
    workflow = {
        "nodes": [agent, model],
        "connections": {
            "OpenRouter Chat Model": {
                "ai_languageModel": [
                    [{"node": "AI Agent", "type": "ai_languageModel", "index": 0}]
                ]
            }
        },
    }

    default_files = generate_project(workflow)
    langgraph_files = generate_project(workflow, langgraph=True)

    assert "create_agent" not in default_files["ai_nodes/ai_agent.py"]
    assert "from langchain.agents import create_agent" in langgraph_files[
        "ai_nodes/ai_agent.py"
    ]
    assert "from langchain_openai import ChatOpenAI" in langgraph_files[
        "ai_nodes/ai_agent.py"
    ]
    assert "from ai_nodes.ai_agent import run_node" in langgraph_files["main.py"]
    assert "langgraph>=1.0" in langgraph_files["requirements.txt"]
    assert "langchain-openai" in langgraph_files["requirements.txt"]
    assert "LANGGRAPH" not in langgraph_files["config.py"]
    for path, content in langgraph_files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")
