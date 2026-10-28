import os
import subprocess
import sys
from pathlib import Path

from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import read_workflow


def test_complete_example_with_mock_services(tmp_path):
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    output = tmp_path / "generated"
    generate_project(read_workflow(path), output)
    (output / "httpx.py").write_text('''
import json

class HTTPError(Exception):
    pass

class Response:
    def __init__(self, value):
        self.value = value
        self.content = b"json"
    def raise_for_status(self):
        pass
    def json(self):
        return self.value

sent = []
prompts = []

def post(url, **kwargs):
    if url == "https://oauth2.googleapis.com/token":
        assert kwargs["data"]["refresh_token"] == "refresh"
        return Response({"access_token": "access"})
    assert url == "https://api.deepseek.com/chat/completions"
    prompts.append(kwargs["json"]["messages"][1]["content"])
    return Response({"choices": [{"message": {"content": "Тестовый отчёт"}}]})

class Client:
    def __init__(self, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def request(self, method, url, **kwargs):
        if method == "GET":
            return Response([{"id": 1, "userId": 2, "title": "Первая", "completed": True}])
        assert method == "POST" and "gmail.googleapis.com" in url
        assert kwargs["headers"]["Authorization"] == "Bearer access"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        sent.append(json.loads(kwargs["content"]))
        return Response({"id": "sent"})
''', encoding="utf-8")
    script = '''
import base64
import email
import io
import zipfile
import httpx
import main
assert main.run() == [{"json": {"id": "sent"}}]
assert "Первая" in httpx.prompts[0] and '"total":1' in httpx.prompts[0]
raw = httpx.sent[0]["raw"]
message = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
parts = message.get_payload()
assert parts[0].get_payload(decode=True).decode("utf-8") == "Тестовый отчёт"
with zipfile.ZipFile(io.BytesIO(parts[1].get_payload(decode=True))) as archive:
    assert archive.namelist() == ["tasks.csv"]
    assert "Первая" in archive.read("tasks.csv").decode("utf-8-sig")
'''
    env = {**os.environ, "DEEPSEEK_API_KEY": "key", "GMAIL_CLIENT_ID": "id",
           "GMAIL_CLIENT_SECRET": "secret", "GMAIL_REFRESH_TOKEN": "refresh"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=output,
                            env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
