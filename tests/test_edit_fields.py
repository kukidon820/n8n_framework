"""Проверки генерации узлов Edit Fields / Set.

Проверяются вычисление assignments и сохранение исходных полей items.
"""

import asyncio

import pytest

from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.generator import generate_project
from n8n_transcriper.versioning import compatible_node


def test_real_settings_and_edit_fields():
    nodes = {
        "Settings": {"name": "Settings", "typeVersion": 3.5, "parameters": {
            "assignments": {"assignments": [
                {"name": "mail_to", "type": "string", "value": "test@example.com"},
            ]},
        }},
        "Edit Fields": {"name": "Edit Fields", "typeVersion": 3.5, "parameters": {
            "assignments": {"assignments": [
                {"name": "report_text", "type": "string", "value": "={{ $json.output }}"},
                {"name": "zip_base64", "type": "string",
                 "value": "={{ $('ZIP - Save attachment base64').first().json.zip_base64 }}"},
                {"name": "mail_to", "type": "string",
                 "value": "={{ $('Settings').first().json.mail_to }}"},
            ]},
        }},
    }
    ns = {}
    source, refs = generate_edit_fields(nodes["Settings"], "settings")
    exec(source, ns)
    settings = asyncio.run(ns["settings"]([{"json": {"ignored": "value"}}], {}, {}))
    assert "ignored" not in settings[0]["json"]
    source, refs = generate_edit_fields(nodes["Edit Fields"], "edit")
    assert refs == {"Settings", "ZIP - Save attachment base64"}
    exec(source, ns)
    result = asyncio.run(ns["edit"]([{"json": {"output": "готово"}}], {
        "Settings": settings, "ZIP - Save attachment base64": [{"json": {"zip_base64": "YWJj"}}]}, {}))
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
    result = asyncio.run(ns["run"]([item], {}, {}))
    assert result == [{"json": {"old": "yes", "new": "hello"}, "binary": item["binary"]}]
    assert item["json"] == {"old": "yes"}


def test_forward_reference_is_reported_and_skipped(tmp_path):
    node = {"id": "a", "name": "Edit", "type": "n8n-nodes-base.set", "typeVersion": 3.5,
        "parameters": {"assignments": {"assignments": [{"name": "x", "type": "string",
            "value": "={{ $('Missing').first().json.x }}"}]}}}
    output = tmp_path / "out"
    files = generate_project({"nodes": [node], "connections": {}}, output)
    assert (output / "main.py").exists()
    assert "Missing" in files["main.py"]
    assert "Edit" in files["main.py"]
    assert not (output / "operations" / "edit.py").exists()


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
    result = asyncio.run(namespace["run"]([{"json": {"count": "12.5", "data": {"ok": True}}}], {}, {}))
    assert result == [{"json": {"count": 12.5, "enabled": False, "object": {"a": 1},
                                "array": [1, 2], "text": '{"ok":true}', "empty": None}}]


def test_invalid_boolean_does_not_become_true():
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {"assignments": {"assignments": [
        {"name": "enabled", "type": "boolean", "value": "no"}]}}}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)
    with pytest.raises(ValueError, match="boolean"):
        asyncio.run(namespace["run"]([{"json": {}}], {}, {}))


def test_ignore_type_conversion_errors_keeps_original_value():
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {
        "options": {"ignoreConversionErrors": True},
        "assignments": {"assignments": [
            {"name": "count", "type": "number", "value": "={{ $json.count }}"},
            {"name": "details", "type": "object", "value": "={{ $json.details }}"},
        ]},
    }}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)

    item = {"json": {"count": "many", "details": "not-json"}}
    assert asyncio.run(namespace["run"]([item], {}, {})) == [{"json": item["json"]}]


def test_conversion_errors_are_not_ignored_by_default():
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {
        "assignments": {"assignments": [
            {"name": "count", "type": "number", "value": "={{ $json.count }}"},
        ]},
    }}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)

    with pytest.raises(ValueError, match="number"):
        asyncio.run(namespace["run"]([{"json": {"count": "many"}}], {}, {}))


@pytest.mark.parametrize("mode,field,expected", [
    ("selected", "includeFields", {"keep": 1}),
    ("except", "excludeFields", {"drop": 2}),
])
def test_select_or_exclude_input_fields(mode, field, expected):
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {
        "includeOtherFields": True, "include": mode, field: "keep", "assignments": {"assignments": []}}}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)
    assert asyncio.run(namespace["run"]([{"json": {"keep": 1, "drop": 2}}], {}, {})) == [{"json": expected}]


def test_include_other_input_and_strip_binary_data():
    node = {"name": "Edit", "typeVersion": 3.5, "parameters": {
        "includeOtherFields": True,
        "options": {"stripBinary": True},
        "assignments": {"assignments": [
            {"name": "added", "type": "string", "value": "yes"},
        ]},
    }}
    source, _ = generate_edit_fields(node, "run")
    namespace = {}
    exec(source, namespace)
    item = {"json": {"original": True}, "binary": {"data": {"data": b"file"}}}

    assert asyncio.run(namespace["run"]([item], {}, {})) == [{"json": {"original": True, "added": "yes"}}]


@pytest.mark.parametrize("include,expected", [
    ("all", {"original": True, "added": "yes"}),
    ("none", {"added": "yes"}),
])
def test_legacy_include_mode_is_respected(include, expected):
    node = {"name": "Legacy Edit", "typeVersion": 3.2, "parameters": {
        "include": include,
        "assignments": {"assignments": [
            {"name": "added", "type": "string", "value": "yes"},
        ]},
    }}
    source, _ = generate_edit_fields(compatible_node(node), "run")
    namespace = {}
    exec(source, namespace)
    item = {"json": {"original": True}, "binary": {"data": {"data": b"file"}}}
    result = asyncio.run(namespace["run"]([item], {}, {}))

    assert result[0]["json"] == expected
    assert result[0]["binary"] == item["binary"]


@pytest.mark.parametrize("version,option,expected_binary", [
    (3.2, "stripBinary", False),
    (3.2, "includeBinary", False),
    (3.6, "includeBinary", True),
    (3.6, "stripBinary", True),
])
def test_known_binary_options_are_not_rejected_by_node_version(
    version, option, expected_binary
):
    node = {
        "name": "Cross-version Edit Fields",
        "type": "n8n-nodes-base.set",
        "typeVersion": version,
        "parameters": {
            "includeOtherFields": True,
            "options": {
                option: not expected_binary if option == "stripBinary" else expected_binary
            },
            "assignments": {"assignments": []},
        },
    }
    source, _ = generate_edit_fields(compatible_node(node), "run")
    namespace = {}
    exec(source, namespace)
    item = {"json": {"keep": True}, "binary": {"data": {"data": b"file"}}}

    result = asyncio.run(namespace["run"]([item], {}, {}))

    assert result[0]["json"] == item["json"]
    assert ("binary" in result[0]) is expected_binary


def test_include_other_fields_is_read_even_for_legacy_type_version():
    node = {
        "name": "Legacy Edit Fields",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.2,
        "parameters": {
            "includeOtherFields": True,
            "assignments": {"assignments": []},
        },
    }
    source, _ = generate_edit_fields(compatible_node(node), "run")
    namespace = {}
    exec(source, namespace)

    assert asyncio.run(namespace["run"]([{"json": {"keep": True}}], {}, {})) == [
        {"json": {"keep": True}}
    ]
