"""Проверки безопасного fallback для неизвестных версий и параметров."""

from n8n_transcriper.versioning import (
    compatible_node,
    manual_parameter_payload,
    node_version_info,
    node_version_warning,
)


def test_unverified_http_version_is_warned_about_and_uses_nearest_fallback():
    node = {
        "name": "HTTP",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 9,
        "parameters": {"url": "https://example.com"},
    }

    assert node_version_info(node)["fallback"] == 4.5
    assert compatible_node(node)["typeVersion"] == 4.5
    assert "9" in node_version_warning(node)


def test_absent_type_version_uses_converter_default_without_warning():
    node = {
        "type": "n8n-nodes-base.httpRequest",
        "parameters": {"url": "https://example.com"},
    }

    assert node_version_info(node) is None
    assert node_version_warning(node) is None


def test_unknown_loop_parameter_is_kept_as_a_path_without_its_value():
    node = {
        "type": "n8n-nodes-base.splitInBatches",
        "typeVersion": 3,
        "parameters": {"batchSize": 5, "futureOption": "sensitive value"},
    }

    payload = manual_parameter_payload(node)

    assert "parameters.futureOption" in payload["unsupported_parameter_paths"]
    assert "sensitive value" not in repr(payload)
