"""Проверки конвертации и выполнения узла No Operation."""

import subprocess
import sys

import pytest

from n8n_transcriper.converters.noop import generate_noop_node
from n8n_transcriper.generator import generate_project
from n8n_transcriper.reader import WorkflowError


def test_noop_converter_passes_through_items_unchanged():
    source = generate_noop_node(
        {"name": "Do nothing", "parameters": {}},
        "do_nothing",
    )
    namespace = {}
    exec(source, namespace)
    items = [{"json": {"answer": 42}, "binary": {"file": {"data": b"bytes"}}}]

    result = namespace["do_nothing"](items)

    assert result is items
    assert result[0] == items[0]


def test_noop_parameters_are_reported_as_unsupported():
    with pytest.raises(WorkflowError, match="No Operation"):
        generate_noop_node(
            {"name": "Do nothing", "parameters": {"unexpected": True}},
            "do_nothing",
        )


def test_noop_is_generated_and_runs_in_a_python_project(tmp_path):
    workflow = {
        "nodes": [{
            "id": "noop-1",
            "name": "Do nothing",
            "type": "n8n-nodes-base.noOp",
            "typeVersion": 1,
            "parameters": {},
        }],
        "connections": {},
    }
    output = tmp_path / "generated"
    files = generate_project(workflow, output)

    assert any(path.startswith("operations/") and path.endswith(".py") for path in files)
    script = (
        "import main; "
        "items = [{'json': {'answer': 42}, 'binary': {'file': {'data': b'bytes'}}}]; "
        "assert main.run(items) == items; print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=output,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
