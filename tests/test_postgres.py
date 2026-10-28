import sys
from types import ModuleType, SimpleNamespace

import pytest

from n8n_transcriper.converters.postgres import generate_postgres_node
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def postgres_node():
    return {"id": "db", "name": "Get users", "type": "n8n-nodes-base.postgres", "typeVersion": 2.8,
            "credentials": {"postgres": {"id": "one", "name": "Database"}},
            "parameters": {"operation": "executeQuery", "query": "SELECT id FROM users WHERE email = $1",
                           "options": {"queryBatching": "independently", "queryReplacement": "={{ $json.email }}"}}}


def test_parameters_are_bound_not_interpolated(monkeypatch):
    calls = []

    class Cursor:
        description = [SimpleNamespace(name="id", type_code=20)]
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params): calls.append((query, params))
        def fetchall(self): return [{"id": 123456789012345678}]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()

    database = ModuleType("database")
    database.__path__ = []
    postgres = ModuleType("database.postgres")
    postgres.connect = lambda timeout: Connection()
    monkeypatch.setitem(sys.modules, "database", database)
    monkeypatch.setitem(sys.modules, "database.postgres", postgres)
    code, _ = generate_postgres_node(postgres_node(), "run")
    namespace = {}
    exec(code, namespace)
    malicious = "x'; DROP TABLE users; --"
    assert namespace["run"]([{"json": {"email": malicious}}], {}, {}) == [{"json": {"id": "123456789012345678"}}]
    assert calls == [("SELECT id FROM users WHERE email = %(value)s", {"value": malicious})]


def test_generator_adds_database_config(tmp_path):
    output = tmp_path / "out"
    generate_project({"nodes": [postgres_node()], "connections": {}}, output)
    assert "POSTGRES_PASSWORD" in (output / "config.py").read_text(encoding="utf-8")
    assert "psycopg[binary]" in (output / "requirements.txt").read_text(encoding="utf-8")
    assert (output / "database" / "postgres.py").exists()


def test_unsafe_query_and_unsupported_batching_fail():
    node = postgres_node()
    node["parameters"]["query"] = "SELECT * FROM users WHERE email = '{{ $json.email }}'"
    with pytest.raises(WorkflowError, match="простой SELECT"):
        generate_postgres_node(node, "run")
    node = postgres_node()
    node["parameters"]["options"]["queryBatching"] = "single"
    with pytest.raises(WorkflowError, match="независимое"):
        generate_postgres_node(node, "run")
