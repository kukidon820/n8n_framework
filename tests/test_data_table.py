"""Проверки генерации Data Table операций через asyncpg."""

import asyncio
import sys
from datetime import datetime, timezone
from types import ModuleType

import pytest

from n8n_transcriper.converters.data_table import (
    DATA_TABLES_MODULE,
    generate_data_table_node,
)
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def data_table_node(operation, **parameters):
    """Создать тестовый узел Data Table с заранее выбранной таблицей."""
    node_parameters = {
        "resource": "row",
        "operation": operation,
        "dataTableId": {"mode": "name", "value": "Customers"},
    }
    node_parameters.update(parameters)
    return {
        "id": "data-table-1",
        "name": f"Customers {operation}",
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "parameters": node_parameters,
    }


class FakeConnection:
    """Хранить вызовы generated SQL и возвращать заданные строки."""

    def __init__(self, *, existing=False, rows=None):
        self.existing = existing
        self.rows = rows or []
        self.calls = []
        self.closed = False

    async def fetch(self, query, *values):
        self.calls.append((query, values))
        if query.startswith("SELECT 1"):
            return [{"exists": 1}] if self.existing else []
        if query.startswith("SELECT *"):
            return self.rows
        if query.startswith(("INSERT", "UPDATE", "DELETE")):
            return self.rows
        raise AssertionError(f"Unexpected SQL: {query}")

    async def execute(self, query, *values):
        self.calls.append((query, values))
        return "OK"

    def transaction(self):
        connection = self

        class Transaction:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        return Transaction()

    async def close(self):
        self.closed = True


def execute_generated_node(monkeypatch, node, connection):
    """Имიტировать asyncpg и запустить сгенерированную корутину."""
    asyncpg = ModuleType("asyncpg")

    async def connect(**kwargs):
        return connection

    asyncpg.connect = connect
    config = ModuleType("config")
    config.POSTGRES_HOST = "localhost"
    config.POSTGRES_PORT = 5432
    config.POSTGRES_DATABASE = "workflow"
    config.POSTGRES_USER = "workflow"
    config.POSTGRES_PASSWORD = "test"
    config.POSTGRES_CONNECT_TIMEOUT = 30
    config.DATA_TABLES = {"Customers": "public.customers"}

    database = ModuleType("database")
    database.__path__ = []
    support = ModuleType("database.data_tables")
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setitem(sys.modules, "database", database)
    monkeypatch.setitem(sys.modules, "database.data_tables", support)
    exec(DATA_TABLES_MODULE, support.__dict__)
    source, _, _ = generate_data_table_node(node, "run_node")
    namespace = {}
    exec(source, namespace)
    return asyncio.run(namespace["run_node"]([{"json": {"id": 7, "email": "a@example.com"}}], {}, {}))


def test_insert_maps_input_and_returns_one_wrapped_item_per_row(monkeypatch):
    connection = FakeConnection(
        rows=[
            {
                "id": 4,
                "email": "a@example.com",
                "createdAt": datetime(2025, 1, 2, tzinfo=timezone.utc),
            }
        ]
    )
    node = data_table_node(
        "insert",
        columns={
            "mappingMode": "defineBelow",
            "value": {"email": "={{ $json.email }}"},
        },
    )

    output = execute_generated_node(monkeypatch, node, connection)

    assert output == [
        {
            "json": {
                "id": 4,
                "email": "a@example.com",
                "createdAt": "2025-01-02T00:00:00+00:00",
            },
            "pairedItem": {"item": 0},
        }
    ]
    assert connection.calls[0][0] == (
        'INSERT INTO "public"."customers" ("email", "createdAt", "updatedAt") '
        'VALUES ($1, now(), now()) RETURNING *'
    )
    assert connection.calls[0][1] == ("a@example.com",)
    assert connection.closed


def test_insert_auto_mapping_drops_datatable_system_columns(monkeypatch):
    connection = FakeConnection(rows=[{"id": 8, "name": "Ada"}])
    node = data_table_node(
        "insert",
        columns={"mappingMode": "autoMapInputData", "value": {}},
    )

    execute_generated_node(monkeypatch, node, connection)

    assert connection.calls[0] == (
        'INSERT INTO "public"."customers" ("email", "createdAt", "updatedAt") '
        'VALUES ($1, now(), now()) RETURNING *',
        ("a@example.com",),
    )


def test_get_compiles_filters_and_returns_each_database_row_as_an_item(monkeypatch):
    connection = FakeConnection(rows=[{"id": 7, "email": "a@example.com"}])
    node = data_table_node(
        "get",
        matchType="allConditions",
        filters={
            "conditions": [
                {"keyName": "id", "condition": "eq", "keyValue": "={{ $json.id }}"}
            ]
        },
        returnAll=False,
        limit=10,
    )

    output = execute_generated_node(monkeypatch, node, connection)

    assert output == [
        {"json": {"id": 7, "email": "a@example.com"}, "pairedItem": {"item": 0}}
    ]
    assert connection.calls[0] == (
        'SELECT * FROM "public"."customers" WHERE "id" = $1 LIMIT $2',
        (7, 10),
    )


def test_update_uses_bound_values_and_requires_a_filter(monkeypatch):
    connection = FakeConnection(rows=[{"id": 7, "email": "updated@example.com"}])
    node = data_table_node(
        "update",
        columns={"mappingMode": "defineBelow", "value": {"email": "updated@example.com"}},
        matchType="allConditions",
        filters={"conditions": [{"keyName": "id", "condition": "eq", "keyValue": 7}]},
    )

    output = execute_generated_node(monkeypatch, node, connection)

    assert output[0]["json"]["email"] == "updated@example.com"
    assert connection.calls[0] == (
        'UPDATE "public"."customers" SET "email" = $1, "updatedAt" = now() '
        'WHERE "id" = $2 RETURNING *',
        ("updated@example.com", 7),
    )
    node["parameters"]["filters"] = {"conditions": []}
    with pytest.raises(WorkflowError, match="требует хотя бы одно условие"):
        generate_data_table_node(node, "run_node")


@pytest.mark.parametrize(
    ("existing", "expected_prefix"),
    [(False, "INSERT INTO"), (True, "UPDATE")],
)
def test_upsert_inserts_or_updates_atomically(monkeypatch, existing, expected_prefix):
    connection = FakeConnection(
        existing=existing,
        rows=[{"id": 7, "email": "a@example.com"}],
    )
    node = data_table_node(
        "upsert",
        columns={
            "mappingMode": "defineBelow",
            "value": {"email": "={{ $json.email }}"},
        },
        matchType="allConditions",
        filters={"conditions": [{"keyName": "id", "condition": "eq", "keyValue": 7}]},
    )

    output = execute_generated_node(monkeypatch, node, connection)

    assert output[0]["json"]["id"] == 7
    assert any(query.startswith("LOCK TABLE") for query, _ in connection.calls)
    assert any(query.startswith(expected_prefix) for query, _ in connection.calls)
    assert connection.closed


def test_delete_returns_deleted_rows_and_requires_a_filter(monkeypatch):
    connection = FakeConnection(rows=[{"id": 7, "email": "a@example.com"}])
    node = data_table_node(
        "deleteRows",
        filters={"conditions": [{"keyName": "id", "condition": "eq", "keyValue": 7}]},
    )

    output = execute_generated_node(monkeypatch, node, connection)

    assert output == [
        {"json": {"id": 7, "email": "a@example.com"}, "pairedItem": {"item": 0}}
    ]
    assert connection.calls[0] == (
        'DELETE FROM "public"."customers" WHERE "id" = $1 RETURNING *',
        (7,),
    )
    node["parameters"]["filters"] = {"conditions": []}
    with pytest.raises(WorkflowError, match="требует хотя бы одно условие"):
        generate_data_table_node(node, "run_node")


def test_generator_adds_async_runner_postgres_config_and_table_mapping():
    workflow = {
        "nodes": [
            data_table_node(
                "get",
                returnAll=True,
                filters={"conditions": []},
            )
        ],
        "connections": {},
    }

    files = generate_project(workflow)

    assert "asyncpg>=0.29" in files["requirements.txt"]
    assert "async def run_async(input_data=None)" in files["main.py"]
    assert "asyncio.run(run_async(input_data))" in files["main.py"]
    assert "await customers_get(items, results, execution)" in files["main.py"]
    assert "POSTGRES_PASSWORD" in files["config.py"]
    assert "DATA_TABLES = {'Customers': 'public.n8n_customers'}" in files["config.py"]
    assert "SELECT * FROM" in files["database/customers_get.py"]
    assert (files["pyproject.toml"]).find("asyncpg>=0.29") >= 0
    for filename, source in files.items():
        if filename.endswith(".py"):
            compile(source, filename, "exec")


def test_unverified_datatable_version_keeps_async_fallback_signature():
    node = data_table_node("get", returnAll=True, filters={"conditions": []})
    node["typeVersion"] = 2

    files = generate_project({"nodes": [node], "connections": {}})

    assert "async def run_node" in files["database/customers_get.py"]
    assert "n8n_manual_parameters=None" in files["database/customers_get.py"]
    assert "TODO: проверить" in files["database/customers_get.py"]
    assert "await customers_get(items, results, execution, n8n_manual_parameters=" in files["main.py"]


def test_unsupported_dry_run_and_table_resource_are_reported():
    node = data_table_node(
        "deleteRows",
        filters={"conditions": [{"keyName": "id", "condition": "eq", "keyValue": 1}]},
        options={"dryRun": True},
    )
    with pytest.raises(WorkflowError, match="dryRun пока не поддерживается"):
        generate_data_table_node(node, "run_node")
    node = data_table_node("insert")
    node["parameters"]["resource"] = "table"
    with pytest.raises(WorkflowError, match="поддерживается ресурс Data Table row"):
        generate_data_table_node(node, "run_node")
