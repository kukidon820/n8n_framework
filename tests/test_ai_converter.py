import sys
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.ai import generate_deepseek_agent
from n8n_transcriper.reader import WorkflowError
from n8n_transcriper.generator import generate_project


def model_node():
    return {
        "id": "model-1", "name": "DeepSeek Chat Model",
        "type": "@n8n/n8n-nodes-langchain.lmChatDeepSeek",
        "parameters": {"model": "deepseek-flash", "options": {
            "temperature": 0.8, "maxTokens": 2500, "timeout": 180000, "maxRetries": 0,
        }},
    }


def agent_node():
    return {
        "id": "agent-1", "name": "AI Agent - Analyze CSV",
        "type": "@n8n/n8n-nodes-langchain.agent",
        "parameters": {"promptType": "define",
                       "text": "={{ JSON.stringify($('Python - Build CSV').first().json.summary) + $json.csv_text }}",
                       "options": {"systemMessage": "Analyze the CSV."}},
    }


def test_real_deepseek_agent_prompt_and_settings(monkeypatch):
    agent = agent_node()
    model = model_node()
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "отчёт"}}]})

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, HTTPError=Exception))
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(DEEPSEEK_API_KEY="test-key"))
    source, refs = generate_deepseek_agent(agent, model, "run")
    namespace = {}
    exec(source, namespace)
    result = namespace["run"]([{"json": {"csv_text": "a;b"}}],
        {"Python - Build CSV": [{"json": {"summary": {"total": 2}}}]}, {})
    assert refs == {"Python - Build CSV"}
    assert result == [{"json": {"output": "отчёт"}}]
    request = calls[0][1]
    assert request["json"]["model"] == "deepseek-flash"
    assert request["json"]["temperature"] == 0.8
    assert request["json"]["max_tokens"] == 2500
    assert request["timeout"] == 180
    assert '"total":2' in request["json"]["messages"][1]["content"]
    assert "a;b" in request["json"]["messages"][1]["content"]


def test_unknown_model_options_fail():
    agent = {"name": "Agent", "parameters": {"promptType": "define", "text": "Hi", "options": {"systemMessage": "System"}}}
    model = {"type": "@n8n/n8n-nodes-langchain.lmChatDeepSeek", "parameters": {"model": "deepseek-flash", "options": {"unknown": 1}}}
    with pytest.raises(WorkflowError, match="настройки модели"):
        generate_deepseek_agent(agent, model, "run")


def test_generator_connects_model_without_executing_it(tmp_path):
    agent = agent_node()
    agent["parameters"] = {**agent["parameters"], "text": "={{ $json.csv_text }}"}
    model = model_node()
    workflow = {"nodes": [agent, model], "connections": {
        model["name"]: {"ai_languageModel": [[{"node": agent["name"], "type": "ai_languageModel", "index": 0}]]}}}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    assert 'DEEPSEEK_API_KEY' in (output / "config.py").read_text(encoding="utf-8")
    assert len(list((output / "ai_nodes").glob("ai_agent_analyze_csv.py"))) == 1
    assert "deepseek-flash" in (output / "ai_nodes" / "ai_agent_analyze_csv.py").read_text(encoding="utf-8")
