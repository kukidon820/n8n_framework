"""Чтение и проверка JSON-файла workflow n8n."""

import json
from pathlib import Path
from typing import Any


class WorkflowError(ValueError):
    """Входной workflow некорректен или содержит неподдерживаемую настройку."""


def read_workflow(path: str | Path) -> dict[str, Any]:
    """Прочитать UTF-8 JSON и проверить структуру workflow для конвертера."""
    try:
        with Path(path).open(encoding="utf-8-sig") as source:
            workflow = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Cannot read workflow {path}: {exc}") from exc
    validate_workflow(workflow)
    return workflow


def validate_workflow(workflow: Any) -> None:
    """Проверить узлы и ссылки connections, нужные для построения графа."""
    if not isinstance(workflow, dict):
        raise WorkflowError("Workflow must be a JSON object")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list):
        raise WorkflowError("Workflow nodes must be a list")
    if not isinstance(connections, dict):
        raise WorkflowError("Workflow connections must be an object")

    names: set[str] = set()
    ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise WorkflowError(f"Node {index} must be an object")
        for key in ("id", "name", "type"):
            if not isinstance(node.get(key), str) or not node[key]:
                raise WorkflowError(f"Node {index}: {key} must be a non-empty string")
        if node["name"] in names or node["id"] in ids:
            raise WorkflowError(f"Node {node['name']}: duplicate name or id")
        if not isinstance(node.get("parameters"), dict):
            raise WorkflowError(f"Node {node['name']}: parameters must be an object")
        names.add(node["name"])
        ids.add(node["id"])

    for source, channels in connections.items():
        if source not in names:
            raise WorkflowError(f"Unknown connection source: {source}")
        if not isinstance(channels, dict):
            raise WorkflowError(f"Connections for {source} must be an object")
        for channel, outputs in channels.items():
            if not isinstance(outputs, list):
                raise WorkflowError(f"{source}: connection {channel} must be a list")
            for output in outputs:
                if not isinstance(output, list):
                    raise WorkflowError(f"{source}: connection output must be a list")
                for target in output:
                    if not isinstance(target, dict) or target.get("node") not in names:
                        raise WorkflowError(f"{source}: unknown or invalid connection target {target!r}")
                    if not isinstance(target.get("type"), str) or not isinstance(target.get("index"), int) or isinstance(target.get("index"), bool) or target["index"] < 0:
                        raise WorkflowError(f"{source}: invalid connection type or index for {target['node']}")
