"""Проверки генерации узлов для файлов и архивов.

Convert to File, ZIP и Extract from File работают с реальными байтами в тестах.
"""

import asyncio
import base64
from io import BytesIO
from zipfile import ZipFile

import pytest

from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.reader import WorkflowError


def test_real_workflow_file_chain():
    nodes = {
        "CSV - Create file": {"name": "CSV - Create file", "type": "n8n-nodes-base.convertToFile",
            "parameters": {"operation": "toText", "sourceProperty": "csv_text",
                          "options": {"addBOM": True, "encoding": "utf8", "fileName": "tasks.csv"}}},
        "ZIP - Compress": {"name": "ZIP - Compress", "type": "n8n-nodes-base.compression",
            "parameters": {"operation": "compress", "fileName": "tasks.zip", "binaryPropertyOutput": "archive"}},
        "ZIP - Save attachment base64": {"name": "ZIP - Save attachment base64", "type": "n8n-nodes-base.extractFromFile",
            "parameters": {"operation": "binaryToPropery", "binaryPropertyName": "archive",
                          "destinationKey": "zip_base64", "options": {"keepSource": "both"}}},
        "ZIP - Decompress for AI": {"name": "ZIP - Decompress for AI", "type": "n8n-nodes-base.compression",
            "parameters": {"operation": "decompress", "binaryPropertyName": "archive"}},
        "CSV - Read file for AI": {"name": "CSV - Read file for AI", "type": "n8n-nodes-base.extractFromFile",
            "parameters": {"operation": "text", "binaryPropertyName": "file_0", "destinationKey": "csv_text",
                          "options": {"encoding": "utf8", "stripBOM": True}}},
    }
    data = [{"json": {"csv_text": "имя;значение\r\nтест;1\r\n", "summary": 1}}]
    for name in ("CSV - Create file", "ZIP - Compress", "ZIP - Save attachment base64",
                 "ZIP - Decompress for AI", "CSV - Read file for AI"):
        namespace = {}
        exec(generate_file_node(nodes[name], "run"), namespace)
        data = asyncio.run(namespace["run"](data))
        if name == "CSV - Create file":
            assert data[0]["binary"]["data"]["data"].startswith(b"\xef\xbb\xbf")
        if name == "ZIP - Save attachment base64":
            archive = data[0]["binary"]["archive"]["data"]
            assert base64.b64decode(data[0]["json"]["zip_base64"]) == archive
            with ZipFile(BytesIO(archive)) as file:
                assert file.namelist() == ["tasks.csv"]
                assert file.read("tasks.csv").decode("utf-8-sig") == "имя;значение\r\nтест;1\r\n"
    assert data[0]["json"]["csv_text"] == "имя;значение\r\nтест;1\r\n"
    assert data[0]["json"]["summary"] == 1


def test_unknown_file_option_fails():
    node = {"name": "file", "type": "n8n-nodes-base.convertToFile", "parameters": {
        "operation": "toText", "sourceProperty": "x", "options": {"unknown": True}}}
    with pytest.raises(WorkflowError, match="unknown"):
        generate_file_node(node, "run")


@pytest.mark.parametrize("version", [1, 1.2, 2.0])
def test_explicit_data_is_base64_is_used_across_node_versions(version):
    node = {
        "name": "Text to Binary",
        "type": "n8n-nodes-base.convertToFile",
        "typeVersion": version,
        "parameters": {
            "operation": "toBinary",
            "sourceProperty": "payload",
            "binaryPropertyName": "file",
            "options": {
                "dataIsBase64": False,
                "fileName": "payload.txt",
                "encoding": "utf8",
            },
        },
    }
    namespace = {}
    exec(generate_file_node(node, "run"), namespace)

    result = asyncio.run(namespace["run"]([{"json": {"payload": "plain text"}}]))

    assert result[0]["binary"]["file"]["data"] == b"plain text"
