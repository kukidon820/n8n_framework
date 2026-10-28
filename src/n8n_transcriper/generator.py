"""Генерация самостоятельного проекта для поддерживаемого линейного workflow."""

import re
from pathlib import Path
from typing import Any

from n8n_transcriper.converters.code import generate_code_node
from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.converters.http import generate_http_node
from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError


def generate_project(workflow: dict[str, Any], output: Path) -> None:
    """Создать проект; неподдерживаемые схемы отклоняются до записи файлов."""
    edges = connections(workflow)
    if any(edge.channel != "main" or edge.target_type != "main" or edge.output != 0 or edge.input != 0 for edge in edges):
        raise WorkflowError("Пока поддерживаются только линейные основные соединения")
    nodes = {node["name"]: node for node in workflow["nodes"]}
    if not nodes:
        raise WorkflowError("Workflow не содержит узлов")
    incoming = {name: 0 for name in nodes}
    outgoing = {name: 0 for name in nodes}
    for edge in edges:
        incoming[edge.target] += 1
        outgoing[edge.source] += 1
    if len(edges) != len(nodes) - 1 or any(v > 1 for v in incoming.values()) or any(v > 1 for v in outgoing.values()):
        raise WorkflowError("Пока поддерживается только одна линейная цепочка узлов")
    order = execution_order(workflow)
    files: dict[str, str] = {}
    imports = []
    calls = []
    converted = 0
    skipped = 0
    dependencies: set[str] = set()
    for index, name in enumerate(order):
        node = nodes[name]
        kind = node["type"]
        if kind == "n8n-nodes-base.manualTrigger":
            if index != 0:
                raise WorkflowError(f"Узел {name}: триггер должен быть первым")
            skipped += 1
            continue
        stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "node"
        stem = f"node_{index:03d}_{stem}"
        function = "run_node"
        if kind == "n8n-nodes-base.code":
            folder = "python_nodes"
            source = generate_code_node(node, function)
            arguments = "items"
        elif kind in {"n8n-nodes-base.convertToFile", "n8n-nodes-base.compression", "n8n-nodes-base.extractFromFile"}:
            folder = "file_nodes"
            source = generate_file_node(node, function)
            arguments = "items"
        elif kind == "n8n-nodes-base.httpRequest":
            folder = "http_requests"
            source = generate_http_node(node, function)
            arguments = "items, results, execution"
            dependencies.add("httpx>=0.27")
        else:
            raise WorkflowError(f"Узел {name}: неподдерживаемый тип {kind}")
        files[f"{folder}/{stem}.py"] = source
        files[f"{folder}/__init__.py"] = ""
        imports.append(f"from {folder}.{stem} import {function} as {stem}")
        calls.extend([f"    logging.info({(name + ': started')!r})", f"    items = {stem}({arguments})", f"    results[{name!r}] = items", f"    logging.info({(name + ': completed')!r})"])
        converted += 1
    if not converted:
        raise WorkflowError("Workflow не содержит исполняемых поддерживаемых узлов")
    files["main.py"] = ('import logging\nimport uuid\n\n' + '\n'.join(imports) +
        '\n\n\ndef run(input_data=None):\n    items = input_data if input_data is not None else [{"json": {}}]\n    results = {}\n    execution = {"id": str(uuid.uuid4())}\n' +
        '\n'.join(calls) + '\n    return items\n\n\nif __name__ == "__main__":\n    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")\n    run()\n')
    files["config.py"] = '"""Настройки авторизации появятся здесь при поддержке credentials."""\n'
    files["requirements.txt"] = ''.join(f'{dependency}\n' for dependency in sorted(dependencies))
    files["README.md"] = '# Сгенерированный проект\n\nУстановите зависимости: `pip install -r requirements.txt`. Запуск: `python main.py`.\n'
    files["conversion_report.md"] = f'# Отчёт о конвертации\n\nУзлов: {len(nodes)}. Преобразовано: {converted}. Триггеров пропущено: {skipped}.\n'
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkflowError(f"Каталог назначения не пуст: {output}")
    for path, content in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
