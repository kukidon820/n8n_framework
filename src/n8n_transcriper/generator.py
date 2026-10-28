"""Генерация самостоятельного проекта для поддерживаемого линейного workflow."""

import re
from pathlib import Path
from typing import Any

from n8n_transcriper.converters.code import generate_code_node
from n8n_transcriper.converters.ai import generate_deepseek_agent
from n8n_transcriper.converters.conditions import generate_condition_node
from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.converters.http import generate_http_node
from n8n_transcriper.converters.postgres import POSTGRES_MODULE, generate_postgres_node
from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.expressions import compile_expression
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
    if any(edge.target_type != "main" or edge.input != 0 or edge.source in model_names or edge.target in model_names for edge in main_edges):
        raise WorkflowError("Неподдерживаемые основные соединения")
    main_nodes = set(nodes) - model_names
    incoming = {name: 0 for name in main_nodes}
    outgoing = {name: 0 for name in main_nodes}
    for edge in main_edges:
        incoming[edge.target] += 1
        outgoing[edge.source] += 1
    if len(main_edges) != len(main_nodes) - 1 or any(v > 1 for v in incoming.values()) or sum(v == 0 for v in incoming.values()) != 1:
        raise WorkflowError("Поддерживается только одно дерево без объединения входов")
    for source in main_nodes:
        ports = [edge.output for edge in main_edges if edge.source == source]
        if len(ports) != len(set(ports)):
            raise WorkflowError(f"Узел {source}: несколько соединений одного выхода пока не поддерживаются")
        if nodes[source]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch"} and (len(ports) > 1 or any(port != 0 for port in ports)):
            raise WorkflowError(f"Узел {source}: неподдерживаемые выходы")
    order = [name for name in execution_order(workflow) if name not in model_names]
    branching = any(nodes[name]["type"] in {"n8n-nodes-base.if", "n8n-nodes-base.switch"} for name in main_nodes)
    parent = {edge.target: edge for edge in main_edges}
    ancestors = {}
    for name in order:
        edge = parent.get(name)
        ancestors[name] = ({edge.source} | ancestors[edge.source]) if edge else set()
    aliases = {name: f"output_{index:03d}" for index, name in enumerate(order)}
    files: dict[str, str] = {}
    imports = []
    calls = []
    converted = 0
    skipped = 0
    dependencies: set[str] = set()
    completed: set[str] = set()
    uses_deepseek = False
    uses_gmail = False
    postgres_credential = None
    for index, name in enumerate(order):
        node = nodes[name]
        kind = node["type"]
        for setting in ("disabled", "alwaysOutputData", "executeOnce", "continueOnFail"):
            if node.get(setting, False) is not False:
                raise WorkflowError(f"Узел {name}: настройка {setting} пока не поддерживается")
        if node.get("onError", "stopWorkflow") != "stopWorkflow":
            raise WorkflowError(f"Узел {name}: настройка onError пока не поддерживается")
        if kind != "n8n-nodes-base.httpRequest" and node.get("retryOnFail", False) is not False:
            raise WorkflowError(f"Узел {name}: настройка retryOnFail пока не поддерживается")
        if kind == "n8n-nodes-base.manualTrigger":
            if index != 0:
                raise WorkflowError(f"Узел {name}: триггер должен быть первым")
            skipped += 1
            if branching:
                calls.append(f"    {aliases[name]} = [items]")
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
            params = node["parameters"]
            values = [("url", params.get("url")), ("jsonBody", params.get("jsonBody"))]
            for key in ("headerParameters", "queryParameters"):
                values.extend((key, pair.get("value")) for pair in params.get(key, {}).get("parameters", []))
            references = set()
            for parameter, value in values:
                if isinstance(value, str):
                    references.update(compile_expression(value, node=name, parameter=parameter).dependencies)
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
            dependencies.add("httpx>=0.27")
            uses_gmail |= node.get("parameters", {}).get("nodeCredentialType") == "gmailOAuth2"
        elif kind == "n8n-nodes-base.set":
            folder = "operations"
            source, references = generate_edit_fields(node, function)
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
        elif kind == "n8n-nodes-base.postgres":
            credential = node.get("credentials", {}).get("postgres", {})
            credential_id = credential.get("id") if isinstance(credential, dict) else None
            if not isinstance(credential_id, str) or not credential_id:
                raise WorkflowError(f"Узел {name}: требуется PostgreSQL credential")
            if postgres_credential is not None and postgres_credential != credential_id:
                raise WorkflowError(f"Узел {name}: несколько PostgreSQL credentials пока не поддерживаются")
            postgres_credential = credential_id
            folder = "database"
            source, references = generate_postgres_node(node, function)
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
            dependencies.add("psycopg[binary]>=3.1")
            files["database/postgres.py"] = POSTGRES_MODULE
        elif kind == "@n8n/n8n-nodes-langchain.agent":
            if name not in models:
                raise WorkflowError(f"Узел {name}: не подключена модель DeepSeek")
            folder = "ai_nodes"
            source, references = generate_deepseek_agent(node, nodes[models[name]], function)
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
            dependencies.add("httpx>=0.27")
            uses_deepseek = True
        elif kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
            folder = "operations"
            source, references, output_count = generate_condition_node(node, function)
            missing = references - (completed & ancestors[name])
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            if any(edge.output >= output_count for edge in main_edges if edge.source == name):
                raise WorkflowError(f"Узел {name}: соединение с несуществующим выходом")
            arguments = "items, results, execution"
        else:
            raise WorkflowError(f"Узел {name}: неподдерживаемый тип {kind}")
        files[f"{folder}/{stem}.py"] = source
        files[f"{folder}/__init__.py"] = ""
        imports.append(f"from {folder}.{stem} import {function} as {stem}")
        if branching:
            edge = parent.get(name)
            if edge:
                calls.append(f"    items = {aliases[edge.source]}[{edge.output}]")
            is_condition = kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}
            count = output_count if is_condition else 1
            calls.extend([f"    {aliases[name]} = [[] for _ in range({count})]", "    if items:",
                          f"        logging.info({(name + ': started')!r})"])
            if is_condition:
                calls.append(f"        {aliases[name]} = {stem}({arguments})")
            else:
                calls.extend([f"        items = {stem}({arguments})", f"        {aliases[name]}[0] = items", f"        results[{name!r}] = items"])
            calls.append(f"        logging.info({(name + ': completed')!r})")
        else:
            calls.extend(["    if not items:", "        return []", f"    logging.info({(name + ': started')!r})", f"    items = {stem}({arguments})", f"    results[{name!r}] = items", f"    logging.info({(name + ': completed')!r})"])
        converted += 1
        if kind not in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
            completed.add(name)
    if not converted:
        raise WorkflowError("Workflow не содержит исполняемых поддерживаемых узлов")
    terminal = [name for name in order if outgoing[name] == 0 and nodes[name]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.manualTrigger"}]
    return_code = ' + '.join(f'{aliases[name]}[0]' for name in terminal) or '[]' if branching else 'items'
    files["main.py"] = ('import logging\nimport uuid\n\n' + '\n'.join(imports) +
        '\n\n\ndef run(input_data=None):\n    items = input_data if input_data is not None else [{"json": {}}]\n    results = {}\n    execution = {"id": str(uuid.uuid4())}\n' +
        '\n'.join(calls) + f'\n    return {return_code}\n\n\nif __name__ == "__main__":\n    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")\n    run()\n')
    config = '"""Настройки авторизации через переменные окружения."""\n\nimport os\n'
    if uses_deepseek:
        config += '\nDEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")\n'
    if uses_gmail:
        config += '\nGMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")\nGMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")\nGMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")\n'
    if postgres_credential is not None:
        config += '\nPOSTGRES_HOST = os.getenv("POSTGRES_HOST", "")\nPOSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))\nPOSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "")\nPOSTGRES_USER = os.getenv("POSTGRES_USER", "")\nPOSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")\n'
    files["config.py"] = config
    files["requirements.txt"] = ''.join(f'{dependency}\n' for dependency in sorted(dependencies))
    required = []
    if uses_deepseek:
        required.append("DEEPSEEK_API_KEY")
    if uses_gmail:
        required.extend(["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"])
    if postgres_credential is not None:
        required.extend(["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DATABASE", "POSTGRES_USER", "POSTGRES_PASSWORD"])
    credentials = ', '.join(f'`{key}`' for key in required) if required else 'не требуются'
    files["README.md"] = ('# Сгенерированный проект\n\n'
        'Установите зависимости: `python -m pip install -r requirements.txt`.\n\n'
        f'Переменные окружения для авторизации: {credentials}.\n\n'
        'Запуск: `python main.py`. Данные подключения читаются из `config.py`.\n')
    files[".gitignore"] = '.env\n.env.*\n!.env.example\n__pycache__/\n*.pyc\n.venv/\n'
    files["conversion_report.md"] = (f'# Отчёт о конвертации\n\nУзлов: {len(nodes)}. '
        f'Преобразовано: {converted}. Моделей подключено: {len(model_names)}. Триггеров пропущено: {skipped}.\n\n'
        f'Настройки авторизации: {credentials}.\n')
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkflowError(f"Каталог назначения не пуст: {output}")
    for path, content in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
