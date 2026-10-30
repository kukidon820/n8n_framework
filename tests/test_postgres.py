"""Проверки generated-кода PostgreSQL-узлов.

Тесты проверяют параметры запросов и транзакции с помощью mock-драйвера.
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from n8n_transcriper.converters.postgres import generate_postgres_node
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def postgres_node():
    """Создать тестовый узел PostgreSQL с параметризованным SELECT-запросом."""
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
    assert calls == [("SELECT id FROM users WHERE email = %(p1)s", {"p1": malicious})]


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


def test_multiple_parameters_are_bound_separately(monkeypatch):
    calls = []
    class Cursor:
        description = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params): calls.append((query, params))
        def fetchall(self): return []
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
    node = postgres_node()
    node["parameters"]["query"] = "SELECT id FROM users WHERE id = $1 AND email = $2"
    node["parameters"]["options"]["queryReplacement"] = "={{ [$json.id, $json.email] }}"
    code, _ = generate_postgres_node(node, "run")
    namespace = {}
    exec(code, namespace)
    namespace["run"]([{"json": {"id": 7, "email": "a,b'"}}], {}, {})
    assert calls == [("SELECT id FROM users WHERE id = %(p1)s AND email = %(p2)s", {"p1": 7, "p2": "a,b'"})]
    node["parameters"]["options"]["queryReplacement"] = "={{ $json.id }},={{ $json.email }}"
    exec(generate_postgres_node(node, "run")[0], namespace)
    namespace["run"]([{"json": {"id": 8, "email": "x,y"}}], {}, {})
    assert calls[-1][1] == {"p1": 8, "p2": "x,y"}


def test_transaction_commits_all_or_rolls_back(monkeypatch):
    committed = []
    connections = []
    class Cursor:
        description = None
        def __init__(self, connection): self.connection = connection
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            assert query == "INSERT INTO users (email) VALUES (%(p1)s)"
            if params["p1"] == "bad":
                raise RuntimeError("database failure")
            self.connection.pending.append(params["p1"])
    class Connection:
        def __init__(self): self.pending = []
        def __enter__(self): return self
        def __exit__(self, exc_type, *_):
            if exc_type is None: committed.extend(self.pending)
        def cursor(self): return Cursor(self)
    def connect(timeout):
        connection = Connection()
        connections.append(connection)
        return connection
    database = ModuleType("database")
    database.__path__ = []
    postgres = ModuleType("database.postgres")
    postgres.connect = connect
    monkeypatch.setitem(sys.modules, "database", database)
    monkeypatch.setitem(sys.modules, "database.postgres", postgres)
    node = postgres_node()
    node["parameters"]["query"] = "INSERT INTO users (email) VALUES ($1)"
    node["parameters"]["options"]["queryBatching"] = "transaction"
    source, _ = generate_postgres_node(node, "run")
    namespace = {}
    exec(source, namespace)
    with pytest.raises(RuntimeError, match="database failure"):
        namespace["run"]([{"json": {"email": "first"}}, {"json": {"email": "bad"}}], {}, {})
    assert committed == []
    assert len(connections) == 1
    assert namespace["run"]([{"json": {"email": "first"}}, {"json": {"email": "second"}}], {}, {}) == [
        {"json": {"success": True}}, {"json": {"success": True}}]
    assert committed == ["first", "second"]
