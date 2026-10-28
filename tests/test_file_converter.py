import base64
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.reader import WorkflowError, read_workflow


def test_real_workflow_file_chain():
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    nodes = {node["name"]: node for node in read_workflow(path)["nodes"]}
    data = [{"json": {"csv_text": "имя;значение\r\nтест;1\r\n", "summary": 1}}]
    for name in ("CSV - Create file", "ZIP - Compress", "ZIP - Save attachment base64",
                 "ZIP - Decompress for AI", "CSV - Read file for AI"):
        namespace = {}
        exec(generate_file_node(nodes[name], "run"), namespace)
        data = namespace["run"](data)
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
