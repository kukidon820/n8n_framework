"""Проверки генерации узлов Edit Fields / Set.

Проверяются вычисление assignments и сохранение исходных полей items.
"""

import pytest

from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError
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
    assert namespace["run"]([item], {}, {}) == [{"json": item["json"]}]


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
        namespace["run"]([{"json": {"count": "many"}}], {}, {})


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
    assert namespace["run"]([{"json": {"keep": 1, "drop": 2}}], {}, {}) == [{"json": expected}]


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

    assert namespace["run"]([item], {}, {}) == [{"json": {"original": True, "added": "yes"}}]


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
    result = namespace["run"]([item], {}, {})

    assert result[0]["json"] == expected
    assert result[0]["binary"] == item["binary"]
