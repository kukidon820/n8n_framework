"""Проверки генерации HTTP Request функций.

Подменённые клиенты позволяют проверять заголовки, тело, авторизацию, retry и ответы.
"""

import sys
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.http import (
    generate_http_node,
    http_credential_fields,
    http_secret_header_fields,
)
from n8n_transcriper.reader import WorkflowError
from n8n_transcriper.generator import generate_project


def test_real_http_node_retries_and_splits_json(monkeypatch):
    node = {
        "name": "HTTP - Get todos", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "retryOnFail": True, "maxTries": 2, "waitBetweenTries": 2000,
        "parameters": {"url": "https://jsonplaceholder.typicode.com/todos", "method": "GET",
                       "options": {"timeout": 30000,
                                   "response": {"response": {"responseFormat": "json"}}}},
    }
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


def test_header_credentials_protect_cross_origin_redirect(monkeypatch):
    import httpx
    requests = []
    real_client = httpx.Client
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://other.example/path"})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "API", "typeVersion": 4.2, "parameters": {"url": "https://example.com",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "options": {"response": {"response": {"responseFormat": "json"}}}},
        "credentials": {"httpHeaderAuth": {"id": "id"}}}
    fields = http_credential_fields(node)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{fields["HEADER_NAME"]: "X-API-Key", fields["HEADER_VALUE"]: "secret"}))
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    assert namespace["request"]([{"json": {}}], {}, {}) == [{"json": {}}]
    assert requests[0].headers["x-api-key"] == "secret"
    assert "x-api-key" not in requests[1].headers


@pytest.mark.parametrize("kind,values", [("httpBasicAuth", {"USERNAME": "user", "PASSWORD": "password"}),
                                         ("httpBearerAuth", {"TOKEN": "secret"})])
def test_auth_is_removed_on_http_to_https_redirect(monkeypatch, kind, values):
    import httpx
    requests = []
    real_client = httpx.Client
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://example.com/path"})
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "API", "typeVersion": 4.2, "parameters": {"url": "http://example.com",
        "authentication": "genericCredentialType", "genericAuthType": kind,
        "options": {"response": {"response": {"responseFormat": "json"}}}},
        "credentials": {kind: {"id": "id"}}}
    fields = http_credential_fields(node)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{fields[key]: value for key, value in values.items()}))
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    namespace["request"]([{"json": {}}], {}, {})
    assert "authorization" in requests[0].headers
    assert "authorization" not in requests[1].headers


def test_generic_query_auth_uses_config_and_is_removed_cross_origin(monkeypatch):
    import httpx

    requests = []
    real_client = httpx.Client

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://other.example/path"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "API", "parameters": {"url": "https://example.com", "authentication": "genericCredentialType",
        "genericAuthType": "httpQueryAuth", "options": {"response": {"response": {"responseFormat": "json"}}}},
        "credentials": {"httpQueryAuth": {"id": "query-credential"}}}
    fields = http_credential_fields(node)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{fields["NAME"]: "api_key", fields["VALUE"]: "secret"}))
    namespace = {}
    source = generate_http_node(node, "request")
    assert "secret" not in source
    exec(source, namespace)
    namespace["request"]([{"json": {}}], {}, {})
    assert requests[0].url.params["api_key"] == "secret"
    assert "api_key" not in requests[1].url.params


def test_cross_origin_credentials_can_be_explicitly_enabled(monkeypatch):
    import httpx

    requests = []
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: (
        requests.append(request),
        httpx.Response(302, headers={"Location": "https://other.example/path"}) if len(requests) == 1 else httpx.Response(200, json={}),
    )[1])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "API", "typeVersion": 4.4, "parameters": {"url": "https://example.com",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "options": {"sendCredentialsOnCrossOriginRedirect": True,
                    "response": {"response": {"responseFormat": "json"}}}},
        "credentials": {"httpHeaderAuth": {"id": "id"}}}
    fields = http_credential_fields(node)
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{fields["HEADER_NAME"]: "X-API-Key", fields["HEADER_VALUE"]: "secret"}))
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    namespace["request"]([{"json": {}}], {}, {})
    assert requests[1].headers["x-api-key"] == "secret"


@pytest.mark.parametrize("method,webdav,accepted", [
    ("GET", False, True), ("PROPFIND", False, False), ("PROPFIND", True, True), ("TRACE", True, False),
])
def test_webdav_methods_require_explicit_option(method, webdav, accepted):
    node = {"name": "HTTP", "parameters": {"url": "https://example.com", "method": method,
        "options": {"webdavMethods": webdav}}}
    if accepted:
        assert "def request" in generate_http_node(node, "request")
    else:
        with pytest.raises(WorkflowError, match="WebDAV"):
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


def test_static_secret_headers_without_credentials_use_config(tmp_path, monkeypatch):
    secret = "Bearer sample-secret-value"
    node = {
        "id": "http-node-with-headers",
        "name": "HTTP Request2",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "parameters": {
            "url": "https://example.com",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Authorization", "value": secret},
                {"name": "Content-Type", "value": "application/json"},
            ]},
        },
    }
    fields = http_secret_header_fields(node)
    field = fields[0]
    source = generate_http_node(node, "request")
    assert secret not in source
    assert f"config.{field}" in source
    assert "'Content-Type': 'application/json'" in source

    output = tmp_path / "generated"
    generate_project({"nodes": [node], "connections": {}}, output)
    config = (output / "config.py").read_text(encoding="utf-8")
    generated_node = next((output / "http_requests").glob("*.py")).read_text(encoding="utf-8")
    assert secret not in config
    assert secret not in generated_node
    assert f'{field} = os.getenv({field!r}, "")' in config
    assert field in (output / "README.md").read_text(encoding="utf-8")

    captured = []

    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def request(self, method, url, **kwargs):
            captured.append(kwargs["headers"])
            return SimpleNamespace(raise_for_status=lambda: None, headers={"content-type": "application/json"},
                                   content=b"{}", json=lambda: {})

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{field: secret}))
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client, HTTPError=Exception))
    namespace = {}
    exec(source, namespace)
    namespace["request"]([{"json": {}}], {}, {})
    assert captured == [{"authorization": secret, "content-type": "application/json"}]


def test_custom_secret_header_is_removed_on_cross_origin_redirect(monkeypatch):
    import httpx

    requests = []
    real_client = httpx.Client

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://other.example/path"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {
        "id": "custom-auth-header",
        "name": "HTTP Request2",
        "typeVersion": 4.2,
        "parameters": {
            "url": "https://example.com",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Authorization", "value": "Bearer sample-secret-value"},
            ]},
        },
    }
    field = http_secret_header_fields(node)[0]
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(**{field: "Bearer sample-secret-value"}))
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    namespace["request"]([{"json": {}}], {}, {})

    assert requests[0].headers["authorization"] == "Bearer sample-secret-value"
    assert "authorization" not in requests[1].headers


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


def test_pagination_fetches_pages_until_empty(monkeypatch):
    import httpx
    requests = []
    sleeps = []
    real_client = httpx.Client
    def handle(request):
        requests.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=[{"page": page}] if page < 3 else [])
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    monkeypatch.setattr("time.sleep", sleeps.append)
    node = {"name": "Pages", "parameters": {"url": "https://example.com/items", "options": {
        "response": {"response": {"responseFormat": "json"}},
        "pagination": {"pagination": {"paginationMode": "updateAParameterInEachRequest",
            "parameters": {"parameters": [{"type": "qs", "name": "page", "value": "={{ $pageCount + 1 }}"}]},
            "paginationCompleteWhen": "responseIsEmpty", "limitPagesFetched": True, "maxRequests": 5,
            "requestInterval": 200}}}}}
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    assert namespace["request"]([{"json": {}}], {}, {}) == [{"json": {"page": 1}}, {"json": {"page": 2}}]
    assert [int(req.url.params["page"]) for req in requests] == [1, 2, 3]
    assert sleeps == [0.2, 0.2]


def test_pagination_requires_supported_completion():
    node = {"name": "Pages", "parameters": {"url": "https://example.com", "options": {
        "pagination": {"pagination": {"paginationMode": "responseContainsNextURL"}}}}}
    with pytest.raises(WorkflowError, match="pagination"):
        generate_http_node(node, "request")


def test_pagination_follows_next_url(monkeypatch):
    import httpx
    seen = []
    real_client = httpx.Client
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"items": [len(seen)], "next": "https://example.com/page2" if len(seen) == 1 else None})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "Pages", "parameters": {"url": "https://example.com/page1", "options": {
        "response": {"response": {"responseFormat": "json"}},
        "pagination": {"pagination": {"paginationMode": "responseContainsNextURL", "nextURL": "={{ $response.body.next }}",
            "paginationCompleteWhen": "other", "completeExpression": "={{ !$response.body.next }}",
            "limitPagesFetched": True, "maxRequests": 5}}}}}
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    result = namespace["request"]([{"json": {}}], {}, {})
    assert seen == ["https://example.com/page1", "https://example.com/page2"]
    assert [item["json"]["items"] for item in result] == [[1], [2]]


def test_full_response_and_never_error(monkeypatch):
    import httpx
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(400, headers={"X-Test": "present"}, json=[{"id": 1}]))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    node = {"name": "HTTP", "parameters": {"url": "https://example.com", "options": {"response": {"response": {
        "responseFormat": "json", "fullResponse": True, "neverError": True}}}}}
    namespace = {}
    exec(generate_http_node(node, "request"), namespace)
    result = namespace["request"]([{"json": {}}], {}, {})
    assert len(result) == 1
    assert result[0]["json"]["statusCode"] == 400
    assert result[0]["json"]["headers"]["x-test"] == "present"
    assert result[0]["json"]["body"] == [{"id": 1}]
