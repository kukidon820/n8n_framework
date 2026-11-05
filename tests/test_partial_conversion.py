"""Проверки частичной конвертации и описания пропущенных узлов."""

from n8n_transcriper.generator import generate_project


def _code(name: str) -> dict:
    return {
        "id": name,
        "name": name,
        "type": "n8n-nodes-base.code",
        "parameters": {"language": "pythonNative", "pythonCode": "return _items"},
    }


def _edge(name: str, channel: str = "main") -> dict:
    return {"node": name, "type": channel, "index": 0}


def test_unsupported_node_is_omitted_and_supported_nodes_are_generated():
    workflow = {
        "nodes": [
            _code("Before"),
            {"id": "x", "name": "Unknown step", "type": "vendor.unknown", "parameters": {}},
            _code("After"),
        ],
        "connections": {
            "Before": {"main": [[_edge("Unknown step")]]},
            "Unknown step": {"main": [[_edge("After")]]},
        },
    }

    files = generate_project(workflow)

    assert "from python_nodes.before import" in files["main.py"]
    assert "from python_nodes.after import" in files["main.py"]
    assert "vendor.unknown" in files["main.py"]
    assert "Unknown step" in files["conversion_report.md"]
    assert not any("unknown_step" in path for path in files)


def test_ai_agent_with_think_tool_is_skipped_for_the_tool_connection():
    workflow = {
        "nodes": [
            _code("Before"),
            {
                "id": "agent",
                "name": "AI Agent",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "parameters": {},
            },
            _code("After"),
            {
                "id": "model",
                "name": "DeepSeek Chat Model",
                "type": "@n8n/n8n-nodes-langchain.lmChatDeepSeek",
                "parameters": {"model": "deepseek-chat"},
            },
            {
                "id": "think",
                "name": "Think Tool",
                "type": "@n8n/n8n-nodes-langchain.toolThink",
                "parameters": {},
            },
        ],
        "connections": {
            "Before": {"main": [[_edge("AI Agent")]]},
            "AI Agent": {"main": [[_edge("After")]]},
            "DeepSeek Chat Model": {"ai_languageModel": [[_edge("AI Agent", "ai_languageModel")]]},
            "Think Tool": {"ai_tool": [[_edge("AI Agent", "ai_tool")]]},
        },
    }

    files = generate_project(workflow)

    assert "from python_nodes.before import" in files["main.py"]
    assert "from python_nodes.after import" in files["main.py"]
    assert "Think Tool" in files["main.py"]
    assert "ai_tool" in files["main.py"]
    assert "другие дополнительные соединения пока не поддерживаются" not in files["main.py"]
    assert not any(path.startswith("ai_nodes/") for path in files)


def test_all_unsupported_nodes_still_produce_a_runnable_project():
    files = generate_project({
        "nodes": [{
            "id": "unknown",
            "name": "Unknown",
            "type": "vendor.unknown",
            "parameters": {},
        }],
        "connections": {},
    })

    assert "main.py" in files
    assert "conversion_report.md" in files
    assert "vendor.unknown" in files["main.py"]
    assert "Преобразовано: 0" in files["conversion_report.md"]
