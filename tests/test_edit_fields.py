from pathlib import Path

import pytest

from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError, read_workflow


def test_real_settings_and_edit_fields():
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    nodes = {node["name"]: node for node in read_workflow(path)["nodes"]}
    ns = {}
    source, refs = generate_edit_fields(nodes["Settings"], "settings")
    exec(source, ns)
    settings = ns["settings"]([{"json": {"ignored": "value"}}], {}, {})
    assert "ignored" not in settings[0]["json"]
    source, refs = generate_edit_fields(nodes["Edit Fields"], "edit")
    assert refs == {"Settings", "ZIP - Save attachment base64"}
    exec(source, ns)
    result = ns["edit"]([{"json": {"output": "готово"}}], {
        "Settings": settings, "ZIP - Save attachment base64": [{"json": {"zip_base64": "YWJj"}}]}, {})
    assert result[0]["json"]["report_text"] == "готово"
    assert result[0]["json"]["zip_base64"] == "YWJj"
    assert result[0]["json"]["mail_to"] == settings[0]["json"]["mail_to"]


def test_includes_fields_and_binary_when_enabled():
    node = {"name": "Edit", "typeVersion": 3.4, "parameters": {"includeOtherFields": True,
        "options": {"stripBinary": False}, "assignments": {"assignments": [
            {"name": "new", "value": "hello", "type": "string"}]}}}
    source, _ = generate_edit_fields(node, "run")
    ns = {}
    exec(source, ns)
    item = {"json": {"old": "yes"}, "binary": {"data": {"data": b"a"}}}
    result = ns["run"]([item], {}, {})
    assert result == [{"json": {"old": "yes", "new": "hello"}, "binary": item["binary"]}]
    assert item["json"] == {"old": "yes"}


def test_forward_reference_rejected_before_output(tmp_path):
    node = {"id": "a", "name": "Edit", "type": "n8n-nodes-base.set", "typeVersion": 3.5,
        "parameters": {"assignments": {"assignments": [{"name": "x", "type": "string",
            "value": "={{ $('Missing').first().json.x }}"}]}}}
    with pytest.raises(WorkflowError, match="Missing"):
        generate_project({"nodes": [node], "connections": {}}, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_typed_assignments_and_expressions():
    assignments = [
        {"name": "count", "type": "number", "value": "={{ $json.count }}"},
        {"name": "enabled", "type": "boolean", "value": "FALSE"},
        {"name": "object", "type": "object", "value": '{"a":1}'},
        {"name": "array", "type": "array", "value": [1, 2]},
        {"name": "text", "type": "string", "value": "={{ $json.data }}"},
        {"name": "empty", "type": "number", "value": None},
    ]
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {"assignments": {"assignments": assignments}}}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)
    result = namespace["run"]([{"json": {"count": "12.5", "data": {"ok": True}}}], {}, {})
    assert result == [{"json": {"count": 12.5, "enabled": False, "object": {"a": 1},
                                "array": [1, 2], "text": '{"ok":true}', "empty": None}}]


def test_invalid_boolean_does_not_become_true():
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {"assignments": {"assignments": [
        {"name": "enabled", "type": "boolean", "value": "no"}]}}}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)
    with pytest.raises(ValueError, match="boolean"):
        namespace["run"]([{"json": {}}], {}, {})
