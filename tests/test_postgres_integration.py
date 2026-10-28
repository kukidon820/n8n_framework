"""Интеграционная проверка с отдельной тестовой базой PostgreSQL."""

import os
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql

from n8n_transcriper.generator import generate_project


@pytest.mark.skipif(not os.getenv("N8N_TEST_POSTGRES_PORT"), reason="Тестовая база PostgreSQL не задана")
def test_generated_transaction_against_postgres(tmp_path):
    port = os.environ["N8N_TEST_POSTGRES_PORT"]
    connection = {"host": "127.0.0.1", "port": int(port), "dbname": "workflow_test",
                  "user": "postgres", "password": "temporary_test_password"}
    table = "workflow_test_" + uuid.uuid4().hex
    node = {"id": "db", "name": "Insert", "type": "n8n-nodes-base.postgres", "typeVersion": 2.8,
            "credentials": {"postgres": {"id": "test"}}, "parameters": {"operation": "executeQuery",
                "query": f"INSERT INTO {table} (value) VALUES ($1)",
                "options": {"queryBatching": "transaction", "queryReplacement": "={{ $json.value }}"}}}
    output = tmp_path / "generated"
    generate_project({"nodes": [node], "connections": {}}, output)
    env = {**os.environ, "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": port,
           "POSTGRES_DATABASE": "workflow_test", "POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "temporary_test_password"}
    try:
        with psycopg.connect(**connection, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE TABLE {} (value TEXT UNIQUE)").format(sql.Identifier(table)))
        script = '''import main
assert main.run([{"json": {"value": "one"}}, {"json": {"value": "two"}}]) == [{"json": {"success": True}}, {"json": {"success": True}}]
try:
    main.run([{"json": {"value": "three"}}, {"json": {"value": "one"}}])
except Exception:
    pass
else:
    raise AssertionError("Ожидалась ошибка ограничения уникальности")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=output, env=env,
                                capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        with psycopg.connect(**connection) as conn:
            rows = conn.execute(sql.SQL("SELECT value FROM {} ORDER BY value").format(sql.Identifier(table))).fetchall()
        assert rows == [("one",), ("two",)]
        select_node = {**node, "name": "Select", "parameters": {"operation": "executeQuery",
            "query": f"SELECT value FROM {table} WHERE value = $1",
            "options": {"queryBatching": "independently", "queryReplacement": "={{ $json.value }}"}}}
        select_output = tmp_path / "select"
        generate_project({"nodes": [select_node], "connections": {}}, select_output)
        select_script = '''import main
assert main.run([{"json": {"value": "one"}}]) == [{"json": {"value": "one"}}]
assert main.run([{"json": {"value": "one' OR 1=1 --"}}]) == []
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", select_script], cwd=select_output,
                                env=env, capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
    finally:
        with psycopg.connect(**connection, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {} ").format(sql.Identifier(table)))
