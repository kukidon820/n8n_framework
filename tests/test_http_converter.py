import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.http import generate_http_node, http_credential_fields
from n8n_transcriper.reader import WorkflowError, read_workflow
from n8n_transcriper.generator import generate_project


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
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [{"id": 1}, {"id": 2}], content=b"json")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=HTTPError))
    monkeypatch.setattr("time.sleep", sleeps.append)
    namespace = {}
    exec(generate_http_node(node, "get_todos"), namespace)
    assert namespace["get_todos"]([{"json": {}}], {}, {}) == [{"json": {"id": 1}}, {"json": {"id": 2}}]
    assert len(calls) == 2
    assert calls[-1][0:2] == ("GET", "https://jsonplaceholder.typicode.com/todos")
    assert sleeps == [2.0]


def test_unsupported_authentication_is_not_ignored():
    node = {"name": "send", "parameters": {"url": "https://example.com", "authentication": "other"}}
    with pytest.raises(WorkflowError, match="аутентификация"):
        generate_http_node(node, "send")


def test_transport_options_batching_and_autodetect(monkeypatch):
    calls, sleeps, settings = [], [], []

    class Client:
        def __init__(self, **kwargs): settings.append(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(raise_for_status=lambda: None, headers={"content-type": "text/plain"}, text="hello")
            return SimpleNamespace(raise_for_status=lambda: None, headers={"content-type": "application/pdf"}, content=b"pdf")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=Exception))
    monkeypatch.setattr("time.sleep", sleeps.append)
    node = {"name": "HTTP", "typeVersion": 4.2, "parameters": {"url": "https://example.com", "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-Test", "value": "v"}]},
        "options": {"batching": {"batch": {"batchSize": 1, "batchInterval": 250}},
                    "allowUnauthorizedCerts": True, "lowercaseHeaders": True,
                    "redirect": {"redirect": {"followRedirects": False, "maxRedirects": 3}},
                    "proxy": "http://proxy.example:8080"}}}
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    result = namespace["request"]([{"json": {"n": 1}}, {"json": {"n": 2}, "binary": {"old": {"data": b"a"}}}], {}, {})
    assert result[0] == {"json": {"data": "hello"}}
    assert result[1]["json"] == {"n": 2}
    assert result[1]["binary"]["old"]["data"] == b"a"
    assert result[1]["binary"]["data"]["data"] == b"pdf"
    assert sleeps == [0.25]
    assert calls[0]["headers"] == {"x-test": "v"}
    assert settings[0] == {"timeout": 300.0, "follow_redirects": False, "max_redirects": 3,
                           "verify": False, "proxy": "http://proxy.example:8080"}


def test_proxy_secrets_not_embedded():
    node = {"name": "HTTP", "parameters": {"url": "https://example.com", "options": {"proxy": "http://user:secret@proxy:8080"}}}
    with pytest.raises(WorkflowError, match="credentials"):
        generate_http_node(node, "request")


@pytest.mark.parametrize("kind,values", [("httpBasicAuth", {"USERNAME": "alice", "PASSWORD": "secret"}),
                                         ("httpBearerAuth", {"TOKEN": "secret"})])
def test_generic_credentials_from_config(monkeypatch, kind, values):
    options = []
    headers = []
    class Client:
        def __init__(self, **kwargs): options.append(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            headers.append(kwargs["headers"])
            return SimpleNamespace(raise_for_status=lambda: None, content=b"{}", json=lambda: {})

    node = {"name": "API", "parameters": {"url": "https://example.com", "authentication": "genericCredentialType",
        "genericAuthType": kind, "options": {"response": {"response": {"responseFormat": "json"}}}},
        "credentials": {kind: {"id": "credential", "name": "credential"}}}
    fields = http_credential_fields(node)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{fields[key]: value for key, value in values.items()}))
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=Exception))
    source = generate_http_node(node, "request")
    assert "secret" not in source
    namespace = {}
    exec(source, namespace)
    assert namespace["request"]([{"json": {}}], {}, {}) == [{"json": {}}]
    if kind == "httpBasicAuth":
        assert options[0]["auth"] == ("alice", "secret")
    else:
        assert headers[0]["Authorization"] == "Bearer secret"


def test_header_credentials_reject_cross_origin_redirect():
    node = {"name": "API", "typeVersion": 4.2, "parameters": {"url": "https://example.com",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"},
        "credentials": {"httpHeaderAuth": {"id": "id"}}}
    with pytest.raises(WorkflowError, match="redirect"):
        generate_http_node(node, "request")


def test_generated_config_contains_only_placeholders(tmp_path):
    node = {"id": "node", "name": "API", "type": "n8n-nodes-base.httpRequest", "parameters": {
        "url": "https://example.com", "authentication": "genericCredentialType", "genericAuthType": "httpBearerAuth",
        "options": {"response": {"response": {"responseFormat": "json"}}}},
        "credentials": {"httpBearerAuth": {"id": "credential", "name": "API"}}}
    output = tmp_path / "generated"
    generate_project({"nodes": [node], "connections": {}}, output)
    field = http_credential_fields(node)["TOKEN"]
    config = (output / "config.py").read_text(encoding="utf-8")
    assert f'{field} = os.getenv(' in config
    assert field in (output / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("body_parameters,expected", [
    ({"contentType": "form-urlencoded", "bodyParameters": {"parameters": [{"name": "name", "value": "={{ $json.name }}"}]}}, ("content", b"name=A%26B")),
    ({"contentType": "json", "bodyParameters": {"parameters": [{"name": "name", "value": "={{ $json.name }}"}]}}, ("content", b'{"name": "A&B"}')),
    ({"contentType": "raw", "body": "={{ $json.name }}", "rawContentType": "text/plain"}, ("content", b"A&B")),
    ({"contentType": "binaryData", "inputDataFieldName": "file"}, ("content", b"hello")),
    ({"contentType": "multipart-form-data", "bodyParameters": {"parameters": [
        {"name": "text", "value": "={{ $json.name }}"},
        {"name": "upload", "parameterType": "formBinaryData", "inputDataFieldName": "file"}]}},
        ("files", [("text", (None, "A&B")), ("upload", ("a.txt", b"hello", "text/plain"))])),
])
def test_request_body_formats(monkeypatch, body_parameters, expected):
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(raise_for_status=lambda: None, content=b"{}", json=lambda: {})
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=Exception))
    params = {"url": "https://example.com", "method": "POST", "sendBody": True,
              "options": {"response": {"response": {"responseFormat": "json"}}}, **body_parameters}
    namespace = {}
    exec(generate_http_node({"name": "HTTP", "parameters": params}, "request"), namespace)
    item = {"json": {"name": "A&B"}, "binary": {"file": {"data": b"hello", "fileName": "a.txt", "mimeType": "text/plain"}}}
    namespace["request"]([item], {}, {})
    assert calls[0][expected[0]] == expected[1]


def test_multipart_is_serialized_by_httpx_without_network(monkeypatch):
    import httpx
    requests = []
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: (requests.append(request), httpx.Response(200, json={}))[1])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "Upload", "parameters": {"url": "https://example.com", "method": "POST", "sendBody": True,
        "contentType": "multipart-form-data", "bodyParameters": {"parameters": [
            {"name": "upload", "parameterType": "formBinaryData", "inputDataFieldName": "file"}]},
        "options": {"response": {"response": {"responseFormat": "json"}}}}}
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    namespace["request"]([{"json": {}, "binary": {"file": {"data": b"actual bytes", "fileName": "a.txt", "mimeType": "text/plain"}}}], {}, {})
    assert requests[0].headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'filename="a.txt"' in requests[0].content
    assert b"actual bytes" in requests[0].content
