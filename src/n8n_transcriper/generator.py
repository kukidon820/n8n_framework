"""Генерация самостоятельного проекта для поддерживаемого линейного workflow."""

import hashlib
import json
import keyword
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from n8n_transcriper.converters.code import generate_code_node
from n8n_transcriper.converters.ai import generate_ai_agent
from n8n_transcriper.converters.conditions import generate_condition_node
from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.converters.files import generate_file_node
from n8n_transcriper.converters.http import (
    generate_http_node,
    http_credential_fields,
    http_secret_header_fields,
)
from n8n_transcriper.converters.merge import generate_merge_node
from n8n_transcriper.converters.postgres import POSTGRES_MODULE, generate_postgres_node
from n8n_transcriper.converters.smtp import (
    generate_email_send_node,
    generate_smtp_node,
    is_gmail_send_node,
)
from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


SUPPORTED_NODE_TYPES = {
    "n8n-nodes-base.code",
    "n8n-nodes-base.convertToFile",
    "n8n-nodes-base.compression",
    "n8n-nodes-base.extractFromFile",
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.emailSend",
    "n8n-nodes-base.set",
    "n8n-nodes-base.postgres",
    "n8n-nodes-base.merge",
    "@n8n/n8n-nodes-langchain.agent",
    "n8n-nodes-base.if",
    "n8n-nodes-base.switch",
}


def node_conversion_issues(workflow: Any) -> list[tuple[str | None, str]]:
    """Проверить узлы независимо друг от друга и собрать их ошибки конвертации."""
    if not isinstance(workflow, dict):
        return []

    connections_data = workflow.get("connections", {})
    model_nodes: set[str] = set()
    agent_models: dict[str, str] = {}
    if isinstance(connections_data, dict):
        for source, channels in connections_data.items():
            if not isinstance(channels, dict):
                continue
            model_outputs = channels.get("ai_languageModel")
            if not isinstance(model_outputs, list):
                continue
            for output in model_outputs:
                if not isinstance(output, list):
                    continue
                for target in output:
                    if isinstance(target, dict) and isinstance(target.get("node"), str):
                        model_nodes.add(source)
                        agent_models[target["node"]] = source

    issues: list[tuple[str | None, str]] = []
    nodes = workflow.get("nodes", [])
    if not isinstance(nodes, list):
        return issues

    nodes_by_name = {
        node["name"]: node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }

    email_smtp_credentials: dict[str, str] = {}
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        name = node.get("name") if isinstance(node.get("name"), str) else None
        label = f"Узел {name}" if name else f"Узел №{index}"
        if not isinstance(kind, str) or not isinstance(node.get("parameters"), dict):
            continue

        for setting in ("disabled", "continueOnFail"):
            if node.get(setting, False) is not False:
                issues.append((name, f"{label}: настройка {setting} не поддерживается."))
        for setting in ("alwaysOutputData", "executeOnce"):
            value = node.get(setting, False)
            if not isinstance(value, bool):
                issues.append((name, f"{label}: настройка {setting} должна быть логическим значением."))
            elif value and kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.merge"}:
                issues.append((name, f"{label}: настройка {setting} для этого типа узла не поддерживается."))
        if node.get("onError", "stopWorkflow") != "stopWorkflow":
            issues.append((name, f"{label}: настройка onError не поддерживается."))
        if kind != "n8n-nodes-base.httpRequest" and node.get("retryOnFail", False) is not False:
            issues.append((name, f"{label}: настройка retryOnFail не поддерживается."))

        trigger_name = kind.rsplit(".", 1)[-1].lower()
        if trigger_name.endswith("trigger") or kind == "n8n-nodes-base.webhook":
            if index != 1:
                issues.append((name, f"{label}: узел запуска должен стоять первым."))
            continue
        if name in model_nodes:
            continue
        if kind not in SUPPORTED_NODE_TYPES:
            issues.append((name, f"{label}: неподдерживаемый тип {kind}"))
            continue

        try:
            if kind == "n8n-nodes-base.code":
                generate_code_node(node, "run_node")
            elif kind in {
                "n8n-nodes-base.convertToFile",
                "n8n-nodes-base.compression",
                "n8n-nodes-base.extractFromFile",
            }:
                generate_file_node(node, "run_node")
            elif kind == "n8n-nodes-base.httpRequest":
                if is_gmail_send_node(node):
                    generate_smtp_node(node, "run_node")
                else:
                    generate_http_node(node, "run_node")
                    http_credential_fields(node)
                    http_secret_header_fields(node)
            elif kind == "n8n-nodes-base.emailSend":
                generate_email_send_node(node, "run_node")
                credentials = node.get("credentials", {})
                credential = credentials.get("smtp", {}) if isinstance(credentials, dict) else {}
                if isinstance(credential, dict):
                    credential_reference = credential.get("id") or credential.get("name")
                    if isinstance(credential_reference, str) and credential_reference:
                        email_smtp_credentials[name or label] = credential_reference
            elif kind == "n8n-nodes-base.set":
                generate_edit_fields(node, "run_node")
            elif kind == "n8n-nodes-base.postgres":
                credentials = node.get("credentials", {})
                credential = credentials.get("postgres", {}) if isinstance(credentials, dict) else {}
                if not isinstance(credential, dict) or not credential.get("id"):
                    issues.append((name, f"{label}: не указаны credentials PostgreSQL."))
                generate_postgres_node(node, "run_node")
            elif kind == "n8n-nodes-base.merge":
                generate_merge_node(node, "run_node")
            elif kind == "@n8n/n8n-nodes-langchain.agent":
                model_name = agent_models.get(name)
                model = nodes_by_name.get(model_name) if model_name else None
                if model is None:
                    issues.append((name, f"{label}: не подключена chat-модель через ai_languageModel."))
                else:
                    generate_ai_agent(node, model, "run_node", "VALIDATION_MODEL")
            elif kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
                generate_condition_node(node, "run_node")
        except Exception as exc:
            message = str(exc) if isinstance(exc, WorkflowError) else (
                f"{label}: не удалось проверить узел ({type(exc).__name__})."
            )
            issues.append((name, message))
    if len(set(email_smtp_credentials.values())) > 1:
        for name in email_smtp_credentials:
            issues.append((name, f"Узел {name}: несколько SMTP credentials в одном проекте не поддерживаются."))
    return issues


def _linked_item_nodes(value: Any) -> set[str]:
    """Найти узлы, на которые выражения ссылаются через paired-item `.item`."""
    if isinstance(value, str):
        if not value.startswith("=") and "{{" not in value:
            return set()
        return {match.group(2) for match in re.finditer(r"""\$\(\s*(['"])(.*?)\1\s*\)\.item(?:\.|\[|$)""", value)}
    if isinstance(value, dict):
        return set().union(*(_linked_item_nodes(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_linked_item_nodes(item) for item in value), set())
    return set()


def _module_names(nodes: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Создать читаемые и устойчивые имена модулей из имён и ID узлов."""
    reserved = {"logging", "uuid", "deepcopy", "run", "postgres", "config", "main", "__init__", "items", "results", "execution", "input_data"}
    reserved.update({"con", "prn", "aux", "nul"})
    reserved.update(f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10))
    candidates = {}
    for name in nodes:
        stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:72] or "node"
        if not stem.isidentifier() or keyword.iskeyword(stem) or stem in reserved or re.fullmatch(r"output_\d+", stem):
            stem = "node_" + stem
        candidates[name] = stem
    counts = Counter(candidates.values())
    result = {}
    for name, stem in candidates.items():
        if counts[stem] > 1:
            suffix = hashlib.sha256(nodes[name]["id"].encode("utf-8")).hexdigest()[:12]
            stem += "_" + suffix
        result[name] = stem
    if len(set(result.values())) != len(result):
        raise WorkflowError("Не удалось построить уникальные имена модулей")
    return result


def _project_toml(dependencies: set[str]) -> str:
    """Описать зависимости generated-проекта в формате uv/PEP 621."""
    dependency_lines = ",\n".join(f"    {json.dumps(value, ensure_ascii=False)}" for value in sorted(dependencies))
    dependency_block = f"[\n{dependency_lines}\n]" if dependency_lines else "[]"
    return (
        "[project]\n"
        'name = "generated-workflow"\n'
        'version = "0.1.0"\n'
        'description = "Самостоятельный Python-проект, созданный из workflow n8n"\n'
        'requires-python = ">=3.12"\n'
        f"dependencies = {dependency_block}\n\n"
        "[tool.uv]\n"
        "package = false\n"
    )


def generate_project(workflow: dict[str, Any], output: Path | None = None) -> dict[str, str]:
    """Сгенерировать исполняемый Python-проект из проверенного workflow.

    Все expressions разрешаются здесь, а не при запуске результата. Если
    настройка или граф неоднозначны, ошибка возникает до создания файлов.
    """
    edges = connections(workflow)
    nodes = {node["name"]: node for node in workflow["nodes"]}
    if not nodes:
        raise WorkflowError("Workflow не содержит узлов")
    models: dict[str, str] = {}
    auxiliary = [edge for edge in edges if edge.channel != "main"]
    for edge in auxiliary:
        if (edge.channel != "ai_languageModel" or edge.target_type != "ai_languageModel" or
                edge.output != 0 or edge.input != 0 or edge.target in models or
                nodes[edge.target]["type"] != "@n8n/n8n-nodes-langchain.agent"):
            raise WorkflowError("Поддерживается одно соединение ai_languageModel на AI Agent; другие дополнительные соединения пока не поддерживаются")
        models[edge.target] = edge.source
    model_names = set(models.values())
    if len(model_names) != len(models):
        raise WorkflowError("Одна модель не может быть подключена к нескольким агентам")
    main_edges = [edge for edge in edges if edge.channel == "main"]
    if any(edge.target_type != "main" or edge.source in model_names or edge.target in model_names or (edge.input != 0 and nodes[edge.target]["type"] != "n8n-nodes-base.merge") for edge in main_edges):
        raise WorkflowError("Неподдерживаемые основные соединения")
    main_nodes = set(nodes) - model_names
    incoming = {name: 0 for name in main_nodes}
    outgoing = {name: 0 for name in main_nodes}
    for edge in main_edges:
        incoming[edge.target] += 1
        outgoing[edge.source] += 1
    if sum(v == 0 for v in incoming.values()) != 1:
        raise WorkflowError("Workflow должен иметь ровно одну начальную точку")
    for source in main_nodes:
        ports = [edge.output for edge in main_edges if edge.source == source]
        if nodes[source]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch"} and any(port != 0 for port in ports):
            raise WorkflowError(f"Узел {source}: неподдерживаемые выходы")
    order = [name for name in execution_order(workflow) if name not in model_names]
    branching = any(
        nodes[name]["type"] in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.merge"}
        or outgoing[name] > 1 or incoming[name] > 1
        for name in main_nodes
    )
    linked_names = set().union(*(_linked_item_nodes(node.get("parameters", {})) for node in nodes.values()), set())
    linked_items = bool(linked_names)
    parents = {name: sorted((edge for edge in main_edges if edge.target == name), key=lambda edge: edge.input) for name in main_nodes}
    # Именованные ссылки разрешены только на узлы, достижимые по main-связям.
    ancestors = {}
    for name in order:
        ancestors[name] = set().union(*({edge.source} | ancestors[edge.source] for edge in parents[name])) if parents[name] else set()
    aliases = {name: f"output_{index:03d}" for index, name in enumerate(order)}
    module_names = _module_names(nodes)
    files: dict[str, str] = {}
    imports = []
    calls = []
    converted = 0
    skipped = 0
    dependencies: set[str] = set()
    completed: set[str] = set()
    ai_model_configs: dict[str, dict[str, Any]] = {}
    uses_gmail = False
    uses_smtp = False
    uses_email_send = False
    email_smtp_credential = None
    http_credentials: set[str] = set()
    http_credential_descriptions: dict[str, set[str]] = {}
    postgres_credential = None
    for index, name in enumerate(order):
        node = nodes[name]
        kind = node["type"]
        for setting in ("disabled", "continueOnFail"):
            if node.get(setting, False) is not False:
                raise WorkflowError(f"Узел {name}: настройка {setting} пока не поддерживается")
        for setting in ("alwaysOutputData", "executeOnce"):
            if not isinstance(node.get(setting, False), bool):
                raise WorkflowError(f"Узел {name}: настройка {setting} должна быть логической")
            if node.get(setting, False) and kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.merge"}:
                raise WorkflowError(f"Узел {name}: настройка {setting} для маршрутизации пока не поддерживается")
        if node.get("onError", "stopWorkflow") != "stopWorkflow":
            raise WorkflowError(f"Узел {name}: настройка onError пока не поддерживается")
        if kind != "n8n-nodes-base.httpRequest" and node.get("retryOnFail", False) is not False:
            raise WorkflowError(f"Узел {name}: настройка retryOnFail пока не поддерживается")
        trigger_name = kind.rsplit(".", 1)[-1].lower()
        if trigger_name.endswith("trigger") or kind == "n8n-nodes-base.webhook":
            if index != 0:
                raise WorkflowError(f"Узел {name}: триггер должен быть первым")
            skipped += 1
            if branching:
                calls.append(f"    {aliases[name]} = [items]")
            continue
        stem = module_names[name]
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
            params = node.get("parameters", {})
            smtp_gmail = is_gmail_send_node(node)
            if smtp_gmail:
                folder = "email_senders"
                source, references = generate_smtp_node(node, function)
                uses_smtp = True
            else:
                folder = "http_requests"
                source = generate_http_node(node, function)
                credential_fields = http_credential_fields(node)
                http_credentials.update(credential_fields.values())
                secret_header_fields = http_secret_header_fields(node)
                http_credentials.update(secret_header_fields.values())
                credential_type = params.get("genericAuthType", "HTTP")
                credentials = node.get("credentials", {})
                credential = credentials.get(credential_type, {}) if isinstance(credentials, dict) else {}
                credential_label = credential.get("name", credential_type) if isinstance(credential, dict) else credential_type
                field_labels = {
                    "USERNAME": "имя пользователя", "PASSWORD": "пароль", "TOKEN": "Bearer token",
                    "HEADER_NAME": "имя заголовка", "HEADER_VALUE": "значение заголовка",
                    "NAME": "имя query-параметра", "VALUE": "значение query-параметра",
                }
                for field, key in credential_fields.items():
                    description = f"HTTP-узел «{name}», credential «{credential_label}»: {field_labels[field]}"
                    http_credential_descriptions.setdefault(key, set()).add(description)
                header_parameters = params.get("headerParameters", {}).get("parameters", [])
                for index, key in secret_header_fields.items():
                    header_name = header_parameters[index]["name"]
                    description = f"HTTP-узел «{name}»: значение секретного заголовка «{header_name}»"
                    http_credential_descriptions.setdefault(key, set()).add(description)
                values = [("url", params.get("url")), ("jsonBody", params.get("jsonBody")), ("body", params.get("body"))]
                for key in ("headerParameters", "queryParameters", "bodyParameters"):
                    values.extend((key, pair.get("value")) for pair in params.get(key, {}).get("parameters", []))
                references = set()
                for parameter, value in values:
                    if isinstance(value, str):
                        references.update(compile_expression(value, node=name, parameter=parameter).dependencies)
                dependencies.add("httpx>=0.27")
                uses_gmail |= params.get("nodeCredentialType") == "gmailOAuth2"
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
        elif kind == "n8n-nodes-base.emailSend":
            folder = "email_senders"
            source, references = generate_email_send_node(node, function)
            credentials = node.get("credentials", {})
            credential = credentials.get("smtp", {}) if isinstance(credentials, dict) else {}
            credential_reference = (
                credential.get("id") or credential.get("name")
                if isinstance(credential, dict)
                else None
            )
            if email_smtp_credential is not None and credential_reference != email_smtp_credential:
                raise WorkflowError(f"Узел {name}: несколько SMTP credentials в одном проекте не поддерживаются")
            email_smtp_credential = credential_reference
            uses_email_send = True
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
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
        elif kind == "n8n-nodes-base.merge":
            folder = "operations"
            source, input_count = generate_merge_node(node, function)
            if [edge.input for edge in parents[name]] != list(range(input_count)):
                raise WorkflowError(f"Узел {name}: Merge требует по одному соединению на каждый вход")
            arguments = "merge_inputs"
        elif kind == "@n8n/n8n-nodes-langchain.agent":
            if name not in models:
                raise WorkflowError(f"Узел {name}: не подключена chat-модель через ai_languageModel")
            folder = "ai_nodes"
            model_name = models[name]
            model_prefix = module_names[model_name].upper()
            source, references, model_config = generate_ai_agent(
                node, nodes[model_name], function, model_prefix
            )
            ai_model_configs[model_prefix] = model_config
            missing = references - (completed & ancestors[name] if branching else completed)
            if missing:
                raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
            arguments = "items, results, execution"
            dependencies.add("httpx>=0.27")
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
            if kind == "n8n-nodes-base.merge":
                values = [f'deepcopy({aliases[e.source]}[{e.output}])' for e in parents[name]]
                inputs_code = f"[{', '.join(values)}]"
                calls.append(f"    merge_inputs = _tag_merge_inputs({inputs_code})" if linked_items else f"    merge_inputs = {inputs_code}")
                calls.append("    items = merge_inputs[0]")
            elif parents[name]:
                calls.append("    items = []")
                for edge in parents[name]:
                    fanout = sum(1 for e in main_edges if e.source == edge.source and e.output == edge.output) > 1
                    value = f"{aliases[edge.source]}[{edge.output}]"
                    copied_value = f"deepcopy({value})" if fanout else value
                    calls.append(f"    items.extend({copied_value})")
            is_condition = kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}
            count = output_count if is_condition else 1
            merge_options = node.get("parameters", {}).get("options", {})
            include_unpaired = isinstance(merge_options, dict) and merge_options.get("includeUnpaired", False)
            merge_params = node.get("parameters", {})
            outer_join = merge_params.get("mode") == "combine" and merge_params.get("combineBy", "combineByFields") == "combineByFields" and merge_params.get("joinMode", "keepMatches") != "keepMatches"
            append_merge = kind == "n8n-nodes-base.merge" and merge_params.get("mode", "append") == "append"
            ready = ("any(merge_inputs)" if include_unpaired or outer_join or append_merge else "all(merge_inputs)") if kind == "n8n-nodes-base.merge" else "items"
            calls.extend([f"    {aliases[name]} = [[] for _ in range({count})]", f"    if {ready}:",
                          f"        logging.info({(name + ': started')!r})"])
            if node.get("executeOnce", False):
                calls.append("        items = items[:1]")
            if is_condition:
                if linked_items:
                    calls.extend([
                        f"        branch_outputs = {stem}({arguments})",
                        f"        {aliases[name]} = _link_outputs(items, branch_outputs, {name!r})",
                    ])
                else:
                    calls.append(f"        {aliases[name]} = {stem}({arguments})")
            else:
                if linked_items and kind != "n8n-nodes-base.merge":
                    calls.append("        source_items = items")
                calls.append(f"        items = {stem}({arguments})")
                if node.get("alwaysOutputData", False):
                    calls.extend(['        if not items:', '            items = [{"json": {}}]'])
                if linked_items:
                    if kind == "n8n-nodes-base.merge":
                        calls.append(f"        items = _link_merge(merge_inputs, items, {name!r})")
                    else:
                        calls.append(f"        items = _link_lineage(source_items, items, {name!r})")
                calls.extend([f"        {aliases[name]}[0] = items", f"        results[{name!r}] = items"])
            calls.append(f"        logging.info({(name + ': completed')!r})")
            if is_condition:
                calls.append(f"    results[{name!r}] = [item for output in {aliases[name]} for item in output]")
        else:
            calls.extend(["    if not items:", "        return []", f"    logging.info({(name + ': started')!r})"])
            if node.get("executeOnce", False):
                calls.append("    items = items[:1]")
            if linked_items:
                calls.append("    source_items = items")
            calls.append(f"    items = {stem}({arguments})")
            if node.get("alwaysOutputData", False):
                calls.extend(['    if not items:', '        items = [{"json": {}}]'])
            if linked_items:
                calls.append(f"    items = _link_lineage(source_items, items, {name!r})")
            calls.extend([f"    results[{name!r}] = items", f"    logging.info({(name + ': completed')!r})"])
        converted += 1
        completed.add(name)
    if not converted:
        raise WorkflowError("Workflow не содержит исполняемых поддерживаемых узлов")
    terminal = [name for name in order if outgoing[name] == 0 and nodes[name]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.manualTrigger"}]
    return_code = ' + '.join(f'{aliases[name]}[0]' for name in terminal) or '[]' if branching else 'items'
    lineage_helpers = ('LINKED_ITEM_NODES = ' + repr(tuple(sorted(linked_names))) + '\n\n\n' + '''def _link_lineage(source_items, output_items, node_name):
    """Перенести paired-item lineage через узел с выходными индексами."""
    for output_index, output_item in enumerate(output_items):
        paired = output_item.get("pairedItem")
        if isinstance(paired, dict):
            source_index = paired.get("item")
        elif isinstance(paired, list) and len(paired) == 1 and isinstance(paired[0], dict):
            source_index = paired[0].get("item")
        elif isinstance(paired, int) and not isinstance(paired, bool):
            source_index = paired
        elif paired is None and len(source_items) == 1:
            source_index = 0
        elif paired is None and len(source_items) == len(output_items):
            source_index = output_index
        else:
            continue
        if not isinstance(source_index, int) or isinstance(source_index, bool) or not 0 <= source_index < len(source_items):
            continue
        lineage = dict(source_items[source_index].get("_n8n_lineage", {}))
        lineage[node_name] = output_index
        output_item["_n8n_lineage"] = lineage
    return output_items


def _link_outputs(source_items, outputs, node_name):
    """Сохранить связь исходных items с выходами IF или Switch."""
    by_identity = {id(item): index for index, item in enumerate(source_items)}
    linked_outputs = [[] for _ in outputs]
    flattened = []
    for port, items in enumerate(outputs):
        for item in items:
            source_index = by_identity.get(id(item))
            if source_index is None:
                continue
            output_item = deepcopy(item)
            lineage = dict(source_items[source_index].get("_n8n_lineage", {}))
            lineage[node_name] = len(flattened)
            output_item["_n8n_lineage"] = lineage
            linked_outputs[port].append(output_item)
            flattened.append(output_item)
    return linked_outputs


def _tag_merge_inputs(inputs):
    """Пометить входные items индексами для восстановления lineage после Merge."""
    for input_index, items in enumerate(inputs):
        for item_index, item in enumerate(items):
            item["__transcriper_input"] = input_index
            item["__transcriper_index"] = item_index
    return inputs


def _link_merge(inputs, output_items, node_name):
    """Восстановить происхождение выходов Merge и проверить неоднозначность."""
    for output_index, item in enumerate(output_items):
        sources = item.pop("__transcriper_sources", None)
        if sources is None and "__transcriper_input" in item:
            sources = [(item["__transcriper_input"], item["__transcriper_index"])]
        inherited = []
        for input_index, item_index in sources or []:
            item.pop("__transcriper_input", None)
            item.pop("__transcriper_index", None)
            if (isinstance(input_index, int) and 0 <= input_index < len(inputs)
                    and isinstance(item_index, int) and 0 <= item_index < len(inputs[input_index])):
                inherited.append(inputs[input_index][item_index].get("_n8n_lineage", {}))
        lineage = {}
        conflicts = set()
        for source_lineage in inherited:
            for ancestor, ancestor_index in source_lineage.items():
                if ancestor in conflicts:
                    continue
                if ancestor in lineage and lineage[ancestor] != ancestor_index:
                    lineage.pop(ancestor)
                    conflicts.add(ancestor)
                else:
                    lineage[ancestor] = ancestor_index
        ambiguous = sorted(conflicts.intersection(LINKED_ITEM_NODES))
        if ambiguous:
            raise ValueError(f"Ссылка .item на узел {ambiguous[0]} неоднозначна после Merge {node_name}")
        lineage[node_name] = output_index
        item["_n8n_lineage"] = lineage
    return output_items


def _clean_items(items):
    """Удалить служебные поля lineage из результата публичной функции run()."""
    for item in items:
        item.pop("_n8n_lineage", None)
        item.pop("__transcriper_input", None)
        item.pop("__transcriper_index", None)
        item.pop("__transcriper_sources", None)
    return items
''') if linked_items else ""
    files["main.py"] = ('"""Точка ручного запуска сгенерированного workflow."""\n\nimport logging\nimport sys\nimport uuid\n' + ('from copy import deepcopy\n' if branching else '') + '\n' + '\n'.join(imports) +
        '\n\n\n' + lineage_helpers + '\n\ndef run(input_data=None):\n    """Выполнить workflow; input_data — список n8n items."""\n    items = input_data if input_data is not None else [{"json": {}}]\n    results = {}\n    execution = {"id": str(uuid.uuid4())}\n' +
        '\n'.join(calls) + f'\n    return {"_clean_items(" + return_code + ")" if linked_items else return_code}\n\n\nif __name__ == "__main__":\n    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)\n    run()\n')
    files["run.bat"] = (
        "@echo off\n"
        "setlocal\n"
        'cd /d "%~dp0"\n'
        "uv run python main.py\n"
        'set "workflow_exit_code=%ERRORLEVEL%"\n'
        'if not "%workflow_exit_code%"=="0" echo Workflow failed with exit code %workflow_exit_code%.\n'
        "echo.\n"
        "pause\n"
        "exit /b %workflow_exit_code%\n"
    )
    files["run.sh"] = (
        "#!/usr/bin/env sh\n"
        "set -u\n"
        'cd "$(dirname "$0")" || exit 1\n'
        "uv run python main.py\n"
        "workflow_exit_code=$?\n"
        'if [ -t 0 ]; then\n'
        '    printf "\\nWorkflow finished with exit code %s.\\n" "$workflow_exit_code"\n'
        '    printf "Press Enter to close this terminal..."\n'
        '    IFS= read -r _\n'
        "fi\n"
        'exit "$workflow_exit_code"\n'
    )
    config = '"""Настройки авторизации через переменные окружения."""\n\nimport os\n'
    for prefix, model_config in sorted(ai_model_configs.items()):
        api_url = model_config["api_url"]
        model_id = model_config["model"]
        options = model_config["options"]
        config += (
            '\n# Настройки подключённой AI-модели; API-ключ задаётся через окружение.\n'
            f'{prefix}_API_URL = os.getenv({(prefix + "_API_URL")!r}, {api_url!r})\n'
            f'{prefix}_API_KEY = os.getenv({(prefix + "_API_KEY")!r}, "")\n'
            f'{prefix}_API_KEY_HEADER = os.getenv({(prefix + "_API_KEY_HEADER")!r}, {model_config["api_key_header"]!r})\n'
            f'{prefix}_API_KEY_PREFIX = os.getenv({(prefix + "_API_KEY_PREFIX")!r}, {model_config["api_key_prefix"]!r})\n'
            f'{prefix}_MODEL = os.getenv({(prefix + "_MODEL")!r}, {model_id!r})\n'
            f'{prefix}_OPTIONS = {options!r}\n'
            f'{prefix}_TIMEOUT = float(os.getenv({(prefix + "_TIMEOUT")!r}, {str(model_config["timeout"])!r}))\n'
            f'{prefix}_MAX_RETRIES = int(os.getenv({(prefix + "_MAX_RETRIES")!r}, {str(model_config["max_retries"])!r}))\n'
        )
    if uses_gmail:
        config += ('\n# OAuth2 credentials Gmail; значения берутся из окружения.\n'
                   'GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")\n'
                   'GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")\n'
                   'GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")\n')
    if uses_smtp or uses_email_send:
        smtp_comment = (
            "SMTP Gmail: используйте полный адрес и пароль приложения, не пароль аккаунта."
            if uses_smtp
            else "SMTP credentials из n8n не экспортируются; заполните параметры подключения здесь."
        )
        smtp_host = "smtp.gmail.com" if uses_smtp else ""
        config += (
            f"\n# {smtp_comment}\n"
            f'SMTP_HOST = os.getenv("SMTP_HOST", {json.dumps(smtp_host)})\n'
            'SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))\n'
            'SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")\n'
            'SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")\n'
            'SMTP_FROM = os.getenv("SMTP_FROM", "")\n'
            'SMTP_HOSTNAME = os.getenv("SMTP_HOSTNAME", "")\n'
            'SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}\n'
            'SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}\n'
        )
    for key in sorted(http_credentials):
        for description in sorted(http_credential_descriptions.get(key, set())):
            config += f'\n# {description}.\n'
        config += f'{key} = os.getenv({key!r}, "")\n'
    if postgres_credential is not None:
        config += ('\n# Подключение к PostgreSQL; пароль не хранится в исходном workflow.\n'
                   'POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")\n'
                   'POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))\n'
                   'POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "")\n'
                   'POSTGRES_USER = os.getenv("POSTGRES_USER", "")\n'
                   'POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")\n')
    files["config.py"] = config
    files["requirements.txt"] = ''.join(f'{dependency}\n' for dependency in sorted(dependencies))
    files["pyproject.toml"] = _project_toml(dependencies)
    files[".python-version"] = "3.12\n"
    required = []
    for prefix, model_config in sorted(ai_model_configs.items()):
        required.append(f"{prefix}_API_KEY")
        if not model_config["api_url"]:
            required.append(f"{prefix}_API_URL")
    if uses_gmail:
        required.extend(["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"])
    if uses_smtp or uses_email_send:
        required.extend(["SMTP_USERNAME", "SMTP_APP_PASSWORD"])
    if uses_email_send and not uses_smtp:
        required.append("SMTP_HOST")
    required.extend(sorted(http_credentials))
    if postgres_credential is not None:
        required.extend(["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DATABASE", "POSTGRES_USER", "POSTGRES_PASSWORD"])
    credentials = ', '.join(f'`{key}`' for key in required) if required else 'не требуются'
    files["README.md"] = (
        '# Сгенерированный проект\n\n'
        'Проект запускает преобразованный workflow как обычную Python-программу. Исходный JSON для запуска не нужен.\n\n'
        '## Требования и установка\n\n'
        'Нужен Python 3.12 или новее и установленный uv. Файл `.python-version` выбирает Python 3.12 для команд uv. '
        'Зависимости указаны в `pyproject.toml`, '
        'точные версии зафиксированы в `uv.lock`. В каталоге проекта выполните:\n\n'
        '```bash\nuv sync --locked\n```\n\n'
        '## Настройка credentials\n\n'
        f'Задайте переменные окружения: {credentials}. Секреты не записывайте в исходники. '
        'В `config.py` перечислены имена используемых переменных.\n\n'
        + ('AI Agent без инструментов выполняется прямым HTTP-запросом к OpenAI-compatible Chat Completions API. '
           'Провайдер задаётся значениями `*_API_URL`, `*_API_KEY`, `*_API_KEY_HEADER`, `*_API_KEY_PREFIX`, `*_MODEL` и `*_OPTIONS` в `config.py`.\n\n'
           if ai_model_configs else '')
        + ('Отправка через Gmail настроена по SMTP: `smtp.gmail.com:587` с STARTTLS. Укажите `SMTP_USERNAME` (полный адрес) и `SMTP_APP_PASSWORD` (пароль приложения), не обычный пароль аккаунта. Пароль приложения создаётся в Google Account: https://myaccount.google.com/apppasswords; для этого должна быть включена двухэтапная аутентификация.\n\n'
           if uses_smtp else '')
        + ('Узел n8n Send Email использует параметры `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME` и `SMTP_APP_PASSWORD` из `config.py` или окружения. Значения SMTP credentials не экспортируются из workflow; заполните их вручную. Для Gmail используйте `smtp.gmail.com:587`, STARTTLS и пароль приложения. Для SSL на порту 465 включите `SMTP_USE_SSL` и выключите `SMTP_STARTTLS`.\n\n'
           if uses_email_send and not uses_smtp else '')
        + ('Узлы Send Email и отправитель Gmail API используют общие параметры SMTP из `config.py`.\n\n'
           if uses_email_send and uses_smtp else '')
        +
        'PowerShell: `$env:ИМЯ_ПЕРЕМЕННОЙ = "значение"`. Linux/macOS: `export ИМЯ_ПЕРЕМЕННОЙ="значение"`.\n\n'
        '## Запуск\n\n'
        'Запустите workflow командой `uv run python main.py` на Windows, Linux и macOS. '
        'Помимо команды выше, используйте `run.bat` на Windows или `sh run.sh` на Linux/macOS. '
        'В интерактивном терминале эти скрипты ждут подтверждения после выполнения, чтобы оставить логи на экране.\n\n'
        '`main.py` можно импортировать из другого кода: `from main import run`; функция `run(input_data)` принимает список items вида `[{"json": {...}}]`.\n'
    )
    files[".gitignore"] = '.env\n.env.*\n!.env.example\n__pycache__/\n*.pyc\n.venv/\n'
    files["conversion_report.md"] = (f'# Отчёт о конвертации\n\nУзлов: {len(nodes)}. '
        f'Преобразовано: {converted}. Моделей подключено: {len(model_names)}. Триггеров пропущено: {skipped}.\n\n'
        f'Настройки авторизации: {credentials}.\n')
    if output is None:
        return files

    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkflowError(f"Каталог назначения не пуст: {output}")
    for path, content in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        newline = "\r\n" if path == "run.bat" else "" if path == "run.sh" else None
        target.write_text(content, encoding="utf-8", newline=newline)
        if path == "run.sh" and os.name != "nt":
            target.chmod(target.stat().st_mode | 0o111)
    return files
