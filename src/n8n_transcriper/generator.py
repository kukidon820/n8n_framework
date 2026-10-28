"""Генерация самостоятельного проекта для поддерживаемого линейного workflow."""

import re
from pathlib import Path
from typing import Any

from n8n_transcriper.converters.code import generate_code_node
from n8n_transcriper.converters.ai import generate_deepseek_agent
from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.converters.http import generate_http_node
from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError


def generate_project(workflow: dict[str, Any], output: Path) -> None:
    """Создать проект; неподдерживаемые схемы отклоняются до записи файлов."""
    edges = connections(workflow)
    nodes = {node["name"]: node for node in workflow["nodes"]}
    if not nodes:
        raise WorkflowError("Workflow не содержит узлов")
    models: dict[str, str] = {}
    auxiliary = [edge for edge in edges if edge.channel != "main"]
    for edge in auxiliary:
        if (edge.channel != "ai_languageModel" or edge.target_type != "ai_languageModel" or
                edge.output != 0 or edge.input != 0 or edge.target in models or
                nodes[edge.source]["type"] != "@n8n/n8n-nodes-langchain.lmChatDeepSeek" or
                nodes[edge.target]["type"] != "@n8n/n8n-nodes-langchain.agent"):
            raise WorkflowError("Пока поддерживаются только линейные основные соединения и одна модель DeepSeek")
        models[edge.target] = edge.source
    model_names = set(models.values())
    if len(model_names) != len(models):
        raise WorkflowError("Одна модель не может быть подключена к нескольким агентам")
    main_edges = [edge for edge in edges if edge.channel == "main"]
    if any(edge.target_type != "main" or edge.output != 0 or edge.input != 0 or edge.source in model_names or edge.target in model_names for edge in main_edges):
        raise WorkflowError("Пока поддерживаются только линейные основные соединения")
    main_nodes = set(nodes) - model_names
    incoming = {name: 0 for name in main_nodes}
    outgoing = {name: 0 for name in main_nodes}
    for edge in main_edges:
        incoming[edge.target] += 1
        outgoing[edge.source] += 1
    if len(main_edges) != len(main_nodes) - 1 or any(v > 1 for v in incoming.values()) or any(v > 1 for v in outgoing.values()):
        raise WorkflowError("Пока поддерживается только одна линейная цепочка узлов")
    order = [name for name in execution_order(workflow) if name not in model_names]
    files: dict[str, str] = {}
    imports = []
    calls = []
    converted = 0
    skipped = 0
    dependencies: set[str] = set()
    completed: set[str] = set()
    uses_deepseek = False
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
        elif kind == "n8n-nodes-base.set":
            folder = "operations"
            source, references = generate_edit_fields(node, function)
            missing = references - completed
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
        elif kind == "@n8n/n8n-nodes-langchain.agent":
            if name not in models:
                raise WorkflowError(f"Узел {name}: не подключена модель DeepSeek")
            folder = "ai_nodes"
            source, references = generate_deepseek_agent(node, nodes[models[name]], function)
            missing = references - completed
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
            dependencies.add("httpx>=0.27")
            uses_deepseek = True
        else:
            raise WorkflowError(f"Узел {name}: неподдерживаемый тип {kind}")
        files[f"{folder}/{stem}.py"] = source
        files[f"{folder}/__init__.py"] = ""
        imports.append(f"from {folder}.{stem} import {function} as {stem}")
        calls.extend([f"    logging.info({(name + ': started')!r})", f"    items = {stem}({arguments})", f"    results[{name!r}] = items", f"    logging.info({(name + ': completed')!r})"])
        converted += 1
        completed.add(name)
    if not converted:
        raise WorkflowError("Workflow не содержит исполняемых поддерживаемых узлов")
    files["main.py"] = ('import logging\nimport uuid\n\n' + '\n'.join(imports) +
        '\n\n\ndef run(input_data=None):\n    items = input_data if input_data is not None else [{"json": {}}]\n    results = {}\n    execution = {"id": str(uuid.uuid4())}\n' +
        '\n'.join(calls) + '\n    return items\n\n\nif __name__ == "__main__":\n    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")\n    run()\n')
    files["config.py"] = ('import os\n\n# Ключ модели DeepSeek; задайте переменную окружения.\nDEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")\n' if uses_deepseek else '"""Настройки авторизации."""\n')
    files["requirements.txt"] = ''.join(f'{dependency}\n' for dependency in sorted(dependencies))
    files["README.md"] = '# Сгенерированный проект\n\nУстановите зависимости: `pip install -r requirements.txt`. Запуск: `python main.py`.\n'
    files["conversion_report.md"] = f'# Отчёт о конвертации\n\nУзлов: {len(nodes)}. Преобразовано: {converted}. Моделей подключено: {len(model_names)}. Триггеров пропущено: {skipped}.\n'
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkflowError(f"Каталог назначения не пуст: {output}")
    for path, content in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
