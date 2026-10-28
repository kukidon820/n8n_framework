import subprocess
import sys
from pathlib import Path

import pytest

from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError, read_workflow


def test_generate_and_run_linear_project(tmp_path):
    workflow = {"nodes": [
        {"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
        {"id": "2", "name": "Build", "type": "n8n-nodes-base.code", "parameters": {
            "language": "pythonNative", "pythonCode": 'return [{"json": {"text": "готово"}}]'}},
        {"id": "3", "name": "File", "type": "n8n-nodes-base.convertToFile", "parameters": {
            "operation": "toText", "sourceProperty": "text", "options": {"fileName": "test.txt"}}},
    ], "connections": {
        "Start": {"main": [[{"node": "Build", "type": "main", "index": 0}]]},
        "Build": {"main": [[{"node": "File", "type": "main", "index": 0}]]},
    }}
    output = tmp_path / "generated"
    generate_project(workflow, output)
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", "import main; print(main.run()[0]['binary']['data']['data'].decode())"],
                            cwd=output, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "готово"
    assert (output / "conversion_report.md").exists()


def test_unsupported_example_fails_without_partial_output(tmp_path):
    path = next(Path(__file__).resolve().parent.parent.glob("HTTP * Email (2).json"))
    output = tmp_path / "generated"
    with pytest.raises(WorkflowError, match="соединения"):
        generate_project(read_workflow(path), output)
    assert not output.exists()
