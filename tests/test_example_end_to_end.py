import os
import subprocess
import sys

from n8n_transcriper.generator import generate_project


def test_file_and_ai_workflow_with_mock_model(tmp_path):
    agent = {
        "id": "agent", "name": "AI Agent - Analyze CSV",
        "type": "@n8n/n8n-nodes-langchain.agent",
        "parameters": {
            "promptType": "define",
            "text": "=Summary {{ JSON.stringify($('Python - Build CSV').first().json.summary) }}\nCSV:\n{{ $json.csv_text }}",
            "options": {"systemMessage": "Analyze the CSV."},
        },
    }
    model = {
        "id": "model", "name": "OpenRouter Chat Model",
        "type": "@n8n/n8n-nodes-langchain.lmChatOpenRouter",
        "parameters": {"model": "openai/gpt-4.1-mini", "options": {
            "temperature": 0.8, "maxTokens": 2500, "timeout": 180000, "maxRetries": 0,
        }},
    }
    nodes = [
        {"id": "build", "name": "Python - Build CSV", "type": "n8n-nodes-base.code",
         "parameters": {"language": "pythonNative",
                        "pythonCode": "return [{'json': {'csv_text': 'title\\nFirst', 'summary': {'total': 1}}}]"}},
        {"id": "create", "name": "CSV - Create file", "type": "n8n-nodes-base.convertToFile",
         "parameters": {"operation": "toText", "sourceProperty": "csv_text",
                        "options": {"addBOM": True, "encoding": "utf8", "fileName": "tasks.csv"}}},
        {"id": "compress", "name": "ZIP - Compress", "type": "n8n-nodes-base.compression",
         "parameters": {"operation": "compress", "fileName": "tasks.zip", "binaryPropertyOutput": "archive"}},
        {"id": "base64", "name": "ZIP - Save attachment base64", "type": "n8n-nodes-base.extractFromFile",
         "parameters": {"operation": "binaryToPropery", "binaryPropertyName": "archive",
                        "destinationKey": "zip_base64", "options": {"keepSource": "both"}}},
        {"id": "decompress", "name": "ZIP - Decompress for AI", "type": "n8n-nodes-base.compression",
         "parameters": {"operation": "decompress", "binaryPropertyName": "archive"}},
        {"id": "extract", "name": "CSV - Read file for AI", "type": "n8n-nodes-base.extractFromFile",
         "parameters": {"operation": "text", "binaryPropertyName": "file_0", "destinationKey": "csv_text",
                        "options": {"encoding": "utf8", "stripBOM": True}}},
        agent,
        model,
    ]
    connections = {
        "Python - Build CSV": {"main": [[{"node": "CSV - Create file", "type": "main", "index": 0}]]},
        "CSV - Create file": {"main": [[{"node": "ZIP - Compress", "type": "main", "index": 0}]]},
        "ZIP - Compress": {"main": [[{"node": "ZIP - Save attachment base64", "type": "main", "index": 0}]]},
        "ZIP - Save attachment base64": {"main": [[{"node": "ZIP - Decompress for AI", "type": "main", "index": 0}]]},
        "ZIP - Decompress for AI": {"main": [[{"node": "CSV - Read file for AI", "type": "main", "index": 0}]]},
        "CSV - Read file for AI": {"main": [[{"node": agent["name"], "type": "main", "index": 0}]]},
        model["name"]: {"ai_languageModel": [[{"node": agent["name"], "type": "ai_languageModel", "index": 0}]]},
    }
    output = tmp_path / "generated"
    generate_project({"nodes": nodes, "connections": connections}, output)
    (output / "httpx.py").write_text('''
class HTTPError(Exception):
    pass

class Response:
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": "analysis complete"}}]}

prompts = []

def post(url, **kwargs):
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    prompts.append(kwargs["json"]["messages"][1]["content"])
    return Response()
''', encoding="utf-8")
    script = '''
import httpx
import main

assert main.run() == [{"json": {"output": "analysis complete"}}]
assert '"total":1' in httpx.prompts[0]
assert "First" in httpx.prompts[0]
'''
    env = {**os.environ, "OPENROUTER_CHAT_MODEL_API_KEY": "test-key"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=output,
                            env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
