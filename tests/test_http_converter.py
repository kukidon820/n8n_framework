import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.http import generate_http_node
from n8n_transcriper.reader import WorkflowError, read_workflow


def test_real_http_node_retries_and_splits_json(monkeypatch):
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    node = next(n for n in read_workflow(path)["nodes"] if n["name"] == "HTTP - Get todos")
    calls = []
    sleeps = []

    class HTTPError(Exception):
        pass

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 30

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if len(calls) == 1:
                raise HTTPError("temporary")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [{"id": 1}, {"id": 2}])

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=HTTPError))
    monkeypatch.setattr("time.sleep", sleeps.append)
    namespace = {}
    exec(generate_http_node(node, "get_todos"), namespace)
    assert namespace["get_todos"]([{"json": {}}], {}, {}) == [{"json": {"id": 1}}, {"json": {"id": 2}}]
    assert len(calls) == 2
    assert calls[-1][0:2] == ("GET", "https://jsonplaceholder.typicode.com/todos")
    assert sleeps == [2.0]


def test_unsupported_authentication_is_not_ignored():
    node = {"name": "send", "parameters": {"url": "https://example.com", "authentication": "predefinedCredentialType"}}
    with pytest.raises(WorkflowError, match="аутентификация"):
        generate_http_node(node, "send")
