"""Проверки узла Loop Over Items и его generated-класса.

Проверяются размеры пакетов, выходы Loop/Done, reset и накопление результатов.
"""

import subprocess
import sys

import pytest

from n8n_transcriper.converters.loop import LOOP_MODULE, generate_loop_node
from n8n_transcriper.generator import generate_project
from n8n_transcriper.graph import execution_order
from n8n_transcriper.reader import WorkflowError


def node(name, kind, parameters=None, version=1):
    """Собрать n8n-узел заданного типа, версии и набора параметров."""
    return {
        "id": name,
        "name": name,
        "type": kind,
        "typeVersion": version,
        "parameters": parameters or {},
    }


def link(name, index=0):
    """Создать связь main-канала на узел с выбранным индексом входа."""
    return {"node": name, "type": "main", "index": index}


def standard_loop_workflow(loop_version=3):
    """Построить полный тестовый цикл с выдачей, обработкой и обратной связью."""
    loop_parameters = {"batchSize": 2, "options": {"reset": False}}
    loop = node("Loop Items", "n8n-nodes-base.splitInBatches", loop_parameters, loop_version)
    if int(loop_version) == 1:
        done_output, loop_output = None, 0
    else:
        done_output, loop_output = (0, 1) if int(loop_version) == 3 else (1, 0)
    loop_outputs = [[] for _ in range(2 if int(loop_version) >= 2 else 1)]
    if done_output is not None:
        loop_outputs[done_output].append(link("Finish"))
    loop_outputs[loop_output].append(link("Increment"))
    nodes = [
        node("Start", "n8n-nodes-base.manualTrigger"),
        node("Build", "n8n-nodes-base.code", {
            "language": "pythonNative",
            "pythonCode": 'return [{"json": {"value": value}} for value in range(5)]',
        }),
        loop,
        node("Increment", "n8n-nodes-base.code", {
            "language": "pythonNative",
            "pythonCode": 'return [{"json": {"value": item["json"]["value"] + 1}} for item in _items]',
        }),
    ]
    if done_output is not None:
        nodes.append(node("Finish", "n8n-nodes-base.code", {
            "language": "pythonNative",
            "pythonCode": "return _items",
        }))
    return {
        "nodes": nodes,
        "connections": {
            "Start": {"main": [[link("Build")]]},
            "Build": {"main": [[link("Loop Items")]]},
            "Loop Items": {"main": loop_outputs},
            "Increment": {"main": [[link("Loop Items")]]},
        },
    }


@pytest.mark.parametrize("version", [1, 2, 3, 3.2])
def test_loop_version_outputs_and_default_batch_sizes(version):
    settings = generate_loop_node(node("Loop", "n8n-nodes-base.splitInBatches", {}, version))

    if int(version) == 3:
        assert settings.loop_output == 1
        assert settings.done_output == 0
        assert settings.batch_size == 1
    else:
        assert settings.loop_output == 0
        assert settings.done_output == (1 if int(version) == 2 else None)
        assert settings.batch_size == 10


def test_loop_settings_reject_invalid_values():
    with pytest.raises(WorkflowError, match="batchSize"):
        generate_loop_node(node("Loop", "n8n-nodes-base.splitInBatches", {"batchSize": 0}, 3))
    with pytest.raises(WorkflowError, match="options.reset"):
        generate_loop_node(node("Loop", "n8n-nodes-base.splitInBatches", {"options": {"reset": "={{ true }}"}}, 3))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_generated_loop_batches_items_and_exposes_done_output(tmp_path, version):
    output = tmp_path / f"generated-v{version}"
    generate_project(standard_loop_workflow(version), output)

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", "import main; print([x['json']['value'] for x in main.run()])"],
        cwd=output,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[1, 2, 3, 4, 5]"
    source = (output / "main.py").read_text(encoding="utf-8")
    assert "loop_loop_items = Loop(batch_size=2, reset=False)" in source
    assert "for loop_batch in loop_loop_items.iterate(items):" in source
    assert (output / "operations" / "loop.py").read_text(encoding="utf-8") == LOOP_MODULE
    assert "Loop Items" in execution_order(standard_loop_workflow(version))


def test_loop_requires_one_feedback_edge():
    workflow = standard_loop_workflow()
    workflow["connections"]["Increment"]["main"] = [[]]

    with pytest.raises(WorkflowError, match="обратная связь"):
        generate_project(workflow)


def test_generated_loop_reset_stops_when_body_returns_no_items(tmp_path):
    workflow = {
        "nodes": [
            node("Start", "n8n-nodes-base.manualTrigger"),
            node("Seed", "n8n-nodes-base.code", {
                "language": "pythonNative",
                "pythonCode": 'return [{"json": {"value": 0}}]',
            }),
            node("Loop Items", "n8n-nodes-base.splitInBatches", {
                "batchSize": 1,
                "options": {"reset": True},
            }, 3),
            node("Advance", "n8n-nodes-base.code", {
                "language": "pythonNative",
                "pythonCode": 'return [{"json": {"value": item["json"]["value"] + 1}} for item in _items if item["json"]["value"] < 3]',
            }),
        ],
        "connections": {
            "Start": {"main": [[link("Seed")]]},
            "Seed": {"main": [[link("Loop Items")]]},
            "Loop Items": {"main": [[], [link("Advance")]]},
            "Advance": {"main": [[link("Loop Items")]]},
        },
    }
    output = tmp_path / "reset-generated"
    generate_project(workflow, output)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", "import main; print(main.run())"],
        cwd=output,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
