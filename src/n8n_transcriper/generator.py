"""Компиляция проверенного workflow n8n в самостоятельный Python-проект.

Модуль связывает конвертеры отдельных типов узлов, проверяет зависимости и
порты, а затем собирает ``main.py``, конфигурацию и только нужные модули.
Сгенерированная программа получает готовые вызовы функций и не читает workflow
при запуске.
"""

import copy
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
from n8n_transcriper.converters.data_table import (
    DATA_TABLES_MODULE,
    generate_data_table_node,
)
from n8n_transcriper.converters.edit_fields import generate_edit_fields
from n8n_transcriper.converters.files import file_node_dependencies, generate_file_node
from n8n_transcriper.converters.http import (
    generate_http_node,
    http_credential_fields,
    http_secret_header_fields,
)
from n8n_transcriper.converters.merge import generate_merge_node
from n8n_transcriper.converters.noop import generate_noop_node
from n8n_transcriper.converters.loop import LOOP_MODULE, generate_loop_node
from n8n_transcriper.converters.postgres import POSTGRES_MODULE, generate_postgres_node
from n8n_transcriper.converters.smtp import (
    generate_email_send_node,
    generate_smtp_node,
    is_gmail_send_node,
)
from n8n_transcriper.converters.wait import generate_wait_node
from n8n_transcriper.graph import Connection, connections, execution_order, loop_feedback_edges
from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError
from n8n_transcriper.versioning import (
    add_manual_parameter_to_source,
    compatible_node,
    manual_parameter_payload,
    node_version_info,
    node_version_warning,
)


SUPPORTED_NODE_TYPES = {
    "n8n-nodes-base.code",
    "n8n-nodes-base.convertToFile",
    "n8n-nodes-base.compression",
    "n8n-nodes-base.extractFromFile",
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.emailSend",
    "n8n-nodes-base.set",
    "n8n-nodes-base.noOp",
    "n8n-nodes-base.postgres",
    "n8n-nodes-base.dataTable",
    "n8n-nodes-base.merge",
    "n8n-nodes-base.splitInBatches",
    "n8n-nodes-base.wait",
    "@n8n/n8n-nodes-langchain.agent",
    "n8n-nodes-base.if",
    "n8n-nodes-base.switch",
}
# Узлы с AI-моделью обрабатываются как зависимость агента, а не как main-шаг.

_MANUAL_FALLBACK_LAYOUTS = {
    "n8n-nodes-base.code": ("python_nodes", "items"),
    "n8n-nodes-base.convertToFile": ("file_nodes", "items"),
    "n8n-nodes-base.compression": ("file_nodes", "items"),
    "n8n-nodes-base.extractFromFile": ("file_nodes", "items"),
    "n8n-nodes-base.httpRequest": ("http_requests", "items, results, execution"),
    "n8n-nodes-base.emailSend": ("email_senders", "items, results, execution"),
    "n8n-nodes-base.set": ("operations", "items, results, execution"),
    "n8n-nodes-base.noOp": ("operations", "items"),
    "n8n-nodes-base.postgres": ("database", "items, results, execution"),
    "n8n-nodes-base.dataTable": ("database", "items, results, execution"),
    "n8n-nodes-base.wait": ("operations", "items, results, execution"),
    "n8n-nodes-base.merge": ("operations", "merge_inputs"),
    "@n8n/n8n-nodes-langchain.agent": ("ai_nodes", "items, results, execution"),
    "n8n-nodes-base.if": ("operations", "items, results, execution"),
    "n8n-nodes-base.switch": ("operations", "items, results, execution"),
}

_UNSUPPORTED_SETTING_MARKERS = (
    "unsupported",
    "not supported",
    "неподдерж",
    "не поддержива",
    "неизвестн",
    "не удалось преобразовать",
    "не найдено выражение",
    "unknown",
)


def _can_generate_manual_fallback(node: dict[str, Any], error: Exception) -> bool:
    """Определить, можно ли заменить неподдержанную часть узла TODO-заготовкой.

    Неизвестная версия допускает fallback сама по себе. Для известной версии
    fallback применяется только к ошибкам, явно указывающим на неизвестную или
    неподдерживаемую настройку; ошибки структуры и графа остаются диагностикой.
    """
    kind = node.get("type")
    if kind not in _MANUAL_FALLBACK_LAYOUTS and kind != "n8n-nodes-base.splitInBatches":
        return False
    if node_version_info(node) is not None:
        return True
    message = str(error).casefold()
    return any(marker in message for marker in _UNSUPPORTED_SETTING_MARKERS)


def _manual_fallback_description(
    node: dict[str, Any],
    payload: dict[str, Any],
    error: Exception | str | None = None,
) -> str:
    """Сформировать безопасную заметку, не копируя значения параметров и секреты."""
    name = str(node.get("name") or "Без имени").replace("\r", " ").replace("\n", " ")
    paths = list(
        payload.get("unsupported_parameter_paths")
        or payload.get("parameter_paths", ())
    )
    if error is not None:
        error_text = str(error).casefold()
        matched_paths = [
            path
            for path in paths
            if str(path).rsplit(".", 1)[-1].split("[", 1)[0].casefold() in error_text
        ]
        if matched_paths:
            paths = matched_paths
    settings = ", ".join(
        str(path).replace("\r", " ").replace("\n", " ") for path in paths
    ) or "параметры узла"
    return (
        f"Узел {name}: сгенерирована ручная TODO-заготовка; проверьте настройки "
        f"({settings}) и реализуйте их в generated-коде."
    )


def _manual_version_fallback_source(
    node: dict[str, Any],
    function_name: str,
    *,
    asynchronous: bool = False,
) -> str:
    """Сгенерировать честный TODO-узел, если новая версия изменила схему.

    Узел явно прерывает запуск до ручной реализации, вместо того чтобы
    возвращать фиктивный результат или молча игнорировать неизвестную настройку.
    """
    name = node.get("name", "<без имени>")
    kind = node.get("type", "<неизвестный тип>")
    version = node.get("typeVersion")
    message = (
        f"Узел {name} ({kind}, typeVersion={version!r}) требует ручной реализации. "
        "Проверьте n8n_manual_parameters и добавьте поведение узла."
    )
    function_prefix = "async def" if asynchronous else "def"
    return (
        f'"""Ручная заготовка для узла {name!r}; автоматическая версия не распознана."""\n\n'
        f"{function_prefix} {function_name}(*args, n8n_manual_parameters=None):\n"
        '    """Заменить эту функцию реализацией исходного узла n8n."""\n'
        "    # TODO: реализовать исходную операцию и настройки узла вручную.\n"
        "    # Структура настроек передана в n8n_manual_parameters['parameters_template'];\n"
        "    # заполните значения и реализуйте неизвестные поля вручную.\n"
        "    # Значения секретов намеренно не копируются из исходного workflow.\n"
        f"    raise NotImplementedError({message!r})\n"
    )


def node_conversion_issues(
    workflow: Any,
    *,
    manual_fallback_warnings: list[tuple[str | None, str]] | None = None,
) -> list[tuple[str | None, str]]:
    """Независимо проверить настройки каждого узла для команды ``validate``.

    Проверяется генерация модуля без построения исполняемой цепочки. Поэтому
    команда может вывести несколько ошибок по разным узлам за один запуск.

    Returns:
        Пары ``(имя узла, сообщение)``. Если имя ещё нельзя определить, вместо
        него возвращается ``None``.
    """
    if not isinstance(workflow, dict):
        return []

    # Модель определяется по auxiliary-связи: её настройки нужны AI Agent,
    # но сама модель не становится отдельным вызовом в main.py.
    connections_data = workflow.get("connections", {})
    model_nodes: set[str] = set()
    agent_model_sources: dict[str, list[str]] = {}
    agent_auxiliary_channels: dict[str, set[str]] = {}
    if isinstance(connections_data, dict):
        for source, channels in connections_data.items():
            if not isinstance(channels, dict):
                continue
            for channel, outputs in channels.items():
                if channel == "main" or not isinstance(outputs, list):
                    continue
                for output in outputs:
                    if not isinstance(output, list):
                        continue
                    for target in output:
                        if not isinstance(target, dict) or not isinstance(target.get("node"), str):
                            continue
                        target_name = target["node"]
                        if channel == "ai_languageModel":
                            model_nodes.add(source)
                            agent_model_sources.setdefault(target_name, []).append(source)
                        else:
                            agent_auxiliary_channels.setdefault(target_name, set()).add(channel)

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
    # Обрабатываем узлы по одному, чтобы ошибка одного конвертера не скрывала остальные.
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        name = node.get("name") if isinstance(node.get("name"), str) else None
        label = f"Узел {name}" if name else f"Узел №{index}"
        if not isinstance(kind, str) or not isinstance(node.get("parameters"), dict):
            continue

        trigger_name = kind.rsplit(".", 1)[-1].lower()
        if trigger_name.endswith("trigger") or kind == "n8n-nodes-base.webhook":
            # Триггеры не преобразуются и не участвуют в проверке настроек узлов.
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
        if kind not in {"n8n-nodes-base.httpRequest", "n8n-nodes-base.splitInBatches"} and node.get("retryOnFail", False) is not False:
            issues.append((name, f"{label}: настройка retryOnFail не поддерживается."))

        if name in model_nodes:
            continue
        if kind not in SUPPORTED_NODE_TYPES:
            issues.append((name, f"{label}: неподдерживаемый тип {kind}"))
            continue

        conversion_node = compatible_node(node)
        compatible_parameters = manual_parameter_payload(node)
        if (
            manual_fallback_warnings is not None
            and compatible_parameters.get("unsupported_parameter_paths")
        ):
            warning = _manual_fallback_description(node, compatible_parameters)
            if (name, warning) not in manual_fallback_warnings:
                manual_fallback_warnings.append((name, warning))
        try:
            if kind == "n8n-nodes-base.code":
                generate_code_node(conversion_node, "run_node")
            elif kind in {
                "n8n-nodes-base.convertToFile",
                "n8n-nodes-base.compression",
                "n8n-nodes-base.extractFromFile",
            }:
                generate_file_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.httpRequest":
                if is_gmail_send_node(node):
                    generate_smtp_node(conversion_node, "run_node")
                else:
                    generate_http_node(conversion_node, "run_node")
                    http_credential_fields(node)
                    http_secret_header_fields(node)
            elif kind == "n8n-nodes-base.emailSend":
                generate_email_send_node(conversion_node, "run_node")
                credentials = node.get("credentials", {})
                credential = credentials.get("smtp", {}) if isinstance(credentials, dict) else {}
                if isinstance(credential, dict):
                    credential_reference = credential.get("id") or credential.get("name")
                    if isinstance(credential_reference, str) and credential_reference:
                        email_smtp_credentials[name or label] = credential_reference
            elif kind == "n8n-nodes-base.set":
                generate_edit_fields(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.noOp":
                generate_noop_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.postgres":
                credentials = node.get("credentials", {})
                credential = credentials.get("postgres", {}) if isinstance(credentials, dict) else {}
                if not isinstance(credential, dict) or not credential.get("id"):
                    issues.append((name, f"{label}: не указаны credentials PostgreSQL."))
                generate_postgres_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.dataTable":
                generate_data_table_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.wait":
                generate_wait_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.merge":
                generate_merge_node(conversion_node, "run_node")
            elif kind == "n8n-nodes-base.splitInBatches":
                generate_loop_node(conversion_node)
            elif kind == "@n8n/n8n-nodes-langchain.agent":
                model_names = agent_model_sources.get(name, [])
                unsupported_channels = sorted(agent_auxiliary_channels.get(name, set()))
                if unsupported_channels:
                    issues.append((name, f"{label}: подключение(я) {', '.join(unsupported_channels)} не поддерживаются; AI Agent нельзя перевести."))
                if len(model_names) != 1:
                    issues.append((name, f"{label}: требуется ровно одно соединение ai_languageModel, найдено {len(model_names)}."))
                    continue
                model = nodes_by_name.get(model_names[0])
                if model is None:
                    issues.append((name, f"{label}: не найдена подключённая chat-модель."))
                else:
                    if not unsupported_channels:
                        generate_ai_agent(conversion_node, model, "run_node", "VALIDATION_MODEL")
            elif kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
                generate_condition_node(conversion_node, "run_node")
        except Exception as exc:
            if (
                isinstance(exc, (WorkflowError, KeyError, TypeError, ValueError))
                and _can_generate_manual_fallback(node, exc)
            ):
                payload = manual_parameter_payload(node, force=True)
                if manual_fallback_warnings is not None:
                    warning = _manual_fallback_description(node, payload, exc)
                    if (name, warning) not in manual_fallback_warnings:
                        manual_fallback_warnings.append((name, warning))
                continue
            message = str(exc) if isinstance(exc, WorkflowError) else (
                f"{label}: не удалось проверить узел ({type(exc).__name__})."
            )
            issues.append((name, message))
    if len(set(email_smtp_credentials.values())) > 1:
        for name in email_smtp_credentials:
            issues.append((name, f"Узел {name}: несколько SMTP credentials в одном проекте не поддерживаются."))
    return issues


def _prepare_partial_workflow(
    workflow: dict[str, Any],
    forced_skips: dict[str, str] | None = None,
    *,
    langgraph: bool = False,
) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    """Исключить непереводимые узлы, соединить их соседей и вернуть причины пропуска.

    Соединения через удаляемые узлы сворачиваются до ближайших оставшихся узлов.
    Для цикла с пропущенным телом путь не добавляется повторно, чтобы не создавать
    некорректный self-loop. Входной workflow остается неизменным.
    """
    source_nodes = {
        node.get("name"): node
        for node in workflow.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }
    skip_reasons: dict[str, list[str]] = {}

    def skip(name: str, reason: str) -> None:
        if name in source_nodes:
            reasons = skip_reasons.setdefault(name, [])
            if reason not in reasons:
                reasons.append(reason)

    for name, reason in (forced_skips or {}).items():
        skip(name, reason)

    issue_workflow = (
        _prepare_langgraph_conversion_input(workflow) if langgraph else workflow
    )
    for name, reason in node_conversion_issues(issue_workflow):
        if name is not None:
            skip(name, reason)

    edges = connections(workflow)
    model_edges_by_agent: dict[str, list[Connection]] = {}
    model_agents_by_source: dict[str, set[str]] = {}
    auxiliary_channels_by_agent: dict[str, set[str]] = {}
    for edge in edges:
        target_node = source_nodes.get(edge.target, {})
        if edge.channel == "ai_languageModel":
            if (
                target_node.get("type") != "@n8n/n8n-nodes-langchain.agent"
                or edge.target_type != "ai_languageModel"
            ):
                skip(edge.target, f"Соединение {edge.channel} имеет неподдерживаемый получатель; соединение пропущено.")
                continue
            model_edges_by_agent.setdefault(edge.target, []).append(edge)
            model_agents_by_source.setdefault(edge.source, set()).add(edge.target)
        elif edge.channel != "main":
            auxiliary_channels_by_agent.setdefault(edge.target, set()).add(edge.channel)
            skip(
                edge.target,
                f"Соединение {edge.channel} не поддерживается; связанный узел пропущен.",
            )

    agent_type = "@n8n/n8n-nodes-langchain.agent"
    for name, node in source_nodes.items():
        if node.get("type") != agent_type:
            continue
        model_edges = model_edges_by_agent.get(name, [])
        if len(model_edges) != 1:
            skip(name, f"AI Agent требует одно соединение ai_languageModel; найдено: {len(model_edges)}.")
        channels = sorted(auxiliary_channels_by_agent.get(name, set()))
        if channels:
            skip(name, f"AI Agent содержит неподдерживаемые соединения: {', '.join(channels)}.")

    for model_name, agent_names in model_agents_by_source.items():
        if len(agent_names) > 1:
            for agent_name in agent_names:
                skip(agent_name, f"Chat Model {model_name} подключена к нескольким AI Agent и не может быть переведена.")

    original_main_edges = [edge for edge in edges if edge.channel == "main"]
    original_outgoing: dict[str, list[Connection]] = {}
    for edge in original_main_edges:
        original_outgoing.setdefault(edge.source, []).append(edge)
    try:
        feedback_edges = set(loop_feedback_edges(workflow))
    except WorkflowError:
        feedback_edges = set()
    for loop_name, node in source_nodes.items():
        if node.get("type") != "n8n-nodes-base.splitInBatches" or loop_name in skip_reasons:
            continue
        loop_settings = generate_loop_node(compatible_node(node))
        pending = [
            edge.target for edge in original_outgoing.get(loop_name, [])
            if edge.output == loop_settings.loop_output
        ]
        body_nodes: set[str] = set()
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == loop_name or current in visited:
                continue
            visited.add(current)
            body_nodes.add(current)
            pending.extend(
                edge.target for edge in original_outgoing.get(current, [])
                if edge not in feedback_edges and edge.target != loop_name
            )
        if body_nodes and body_nodes <= set(skip_reasons):
            skip(
                loop_name,
                "Тело Loop Over Items целиком состоит из непереводимых узлов; цикл пропущен.",
            )

    skipped_agents = {
        name for name, node in source_nodes.items()
        if node.get("type") == agent_type and name in skip_reasons
    }
    all_model_sources = set(model_agents_by_source)
    retained_model_sources = {
        source
        for source, agent_names in model_agents_by_source.items()
        if agent_names - skipped_agents
    }
    for model_name in all_model_sources - retained_model_sources:
        skip(model_name, "Chat Model пропущена, поскольку подключённый к ней AI Agent нельзя перевести.")

    if not skip_reasons:
        return workflow, []

    removed = set(skip_reasons)
    outgoing: dict[str, list[Connection]] = {}
    for edge in edges:
        if edge.channel == "main":
            outgoing.setdefault(edge.source, []).append(edge)

    rewritten: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}
    emitted: set[tuple[str, int, str, int]] = set()

    def add_edge(source: str, output: int, target: str, target_input: int) -> None:
        key = (source, output, target, target_input)
        if key in emitted:
            return
        emitted.add(key)
        channels = rewritten.setdefault(source, {})
        outputs = channels.setdefault("main", [])
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": target_input})

    def forward(
        source: str,
        source_output: int,
        target: str,
        target_input: int,
        visited: frozenset[str],
    ) -> None:
        if target not in removed:
            add_edge(source, source_output, target, target_input)
            return
        if target in visited:
            return
        next_visited = visited | {target}
        for next_edge in outgoing.get(target, []):
            forward(source, source_output, next_edge.target, next_edge.input, next_visited)

    for edge in edges:
        if edge.channel == "main" and edge.source not in removed:
            forward(edge.source, edge.output, edge.target, edge.input, frozenset())
        elif (
            edge.channel == "ai_languageModel"
            and edge.source not in removed
            and edge.target not in removed
            and edge.target_type == "ai_languageModel"
        ):
            channels = rewritten.setdefault(edge.source, {})
            outputs = channels.setdefault(edge.channel, [])
            while len(outputs) <= edge.output:
                outputs.append([])
            outputs[edge.output].append({
                "node": edge.target,
                "type": edge.target_type,
                "index": edge.input,
            })

    candidate = copy.deepcopy(workflow)
    candidate["nodes"] = [
        node for node in workflow["nodes"]
        if isinstance(node, dict) and node.get("name") not in removed
    ]
    candidate["connections"] = rewritten
    notes = [
        (
            str(node.get("name", "<без имени>")),
            str(node.get("type", "<неизвестный тип>")),
            "; ".join(skip_reasons[node["name"]]),
        )
        for node in workflow["nodes"]
        if isinstance(node, dict) and node.get("name") in skip_reasons
    ]
    return candidate, notes


def _append_skipped_part_notes(
    files: dict[str, str], skipped_parts: list[tuple[str, str, str]]
) -> None:
    """Добавить комментарии и пояснения для каждой пропущенной части n8n."""
    if not skipped_parts:
        return

    safe_notes = [
        (name.replace("\r", " ").replace("\n", " "),
         kind.replace("\r", " ").replace("\n", " "),
         reason.replace("\r", " ").replace("\n", " "))
        for name, kind, reason in skipped_parts
    ]
    comment_lines = [
        "# Часть исходного workflow пропущена: конвертер не смог перевести её поведение.",
        "# Связи через пропущенные узлы упрощены; проверьте входные данные оставшихся узлов.",
        *(
            f"# Не переведён узел {name!r} ({kind}): {reason}"
            for name, kind, reason in safe_notes
        ),
    ]
    main = files.get("main.py", "")
    marker = "def run(input_data=None):"
    if marker in main:
        files["main.py"] = main.replace(marker, "\n".join(comment_lines) + "\n\n" + marker, 1)
    else:
        files["main.py"] = "\n".join(comment_lines) + "\n\n" + main

    markdown_notes = "\n".join(
        f"- **{name}** (`{kind}`): {reason}" for name, kind, reason in safe_notes
    )
    files["README.md"] = files.get("README.md", "") + (
        "\n## Не переведённые части workflow\n\n"
        "Эти узлы и их операции не включены в Python-проект. Связи через них упрощены, "
        "а их исходное поведение нужно реализовать вручную.\n\n"
        + markdown_notes
        + "\n"
    )
    files["conversion_report.md"] = files.get("conversion_report.md", "") + (
        f"\n\nПропущено частей workflow: {len(safe_notes)}.\n\n{markdown_notes}\n"
    )


def _write_project_files(files: dict[str, str], output: Path | None) -> None:
    """Записать файлы проекта после добавления всех замечаний о частичной конвертации."""
    if output is None:
        return
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


def _linked_item_nodes(value: Any) -> set[str]:
    """Найти имена узлов из выражений, использующих paired-item ``.item``.

    Результат нужен generated-коду, чтобы включить только тогда, когда требуется,
    служебные поля lineage и функции переноса связей между items.
    """
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
    """Преобразовать имена n8n-узлов в уникальные имена Python-модулей.

    Имена приводятся к нижнему snake_case; ключевые слова и запрещённые имена
    Windows получают префикс. Если два имени совпали после нормализации, к ним
    добавляется стабильный короткий хеш ID узла.
    """
    reserved = {"logging", "uuid", "deepcopy", "run", "postgres", "data_tables", "config", "main", "__init__", "items", "results", "execution", "input_data"}
    reserved.update({"con", "prn", "aux", "nul"})
    reserved.update(f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10))
    # Сначала нормализуем имена, затем отдельно разрешаем совпадения.
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


def _default_data_table_name(mode: str, reference: str) -> str:
    """Создать стабильное имя PostgreSQL-таблицы для ссылки из n8n."""
    if mode == "name":
        name = re.sub(r"[^a-z0-9_]+", "_", reference.lower()).strip("_")
        if name:
            if name[0].isdigit():
                name = "t_" + name
            return "n8n_" + name[:48]
    suffix = hashlib.sha256(reference.encode("utf-8")).hexdigest()[:16]
    return f"n8n_data_table_{suffix}"


def _project_toml(dependencies: set[str]) -> str:
    """Собрать минимальный ``pyproject.toml`` с зависимостями generated-проекта.

    Зависимости сортируются для стабильного результата, а generated-проект
    запускается без установки самого каталога как Python-пакета.
    """
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


def _loop_layout(
    nodes: dict[str, dict[str, Any]],
    main_nodes: set[str],
    main_edges: list[Any],
    base_order: list[str],
    feedback_edges: list[Any],
) -> dict[str, Any] | None:
    """Проверить поддерживаемую схему Loop и разделить граф на три области.

    Сейчас один workflow может содержать один Loop с одним начальным входом и
    одной обратной связью. Возвращаемая структура хранит узлы до цикла, узлы
    тела, done-ветвь и порядок, в котором их нужно сгенерировать.

    Returns:
        Сведения о цикле или ``None``, если в графе нет Loop Over Items.

    Raises:
        WorkflowError: если у цикла нет единственного входа/обратной связи,
            ветви пересекаются или в workflow есть узлы вне этих областей.
    """
    # Ограничение на один цикл позволяет сгенерировать понятный вложенный блок
    # в main.py без runtime-планировщика и универсального движка графов.
    loop_names = [
        name for name in main_nodes
        if nodes[name]["type"] == "n8n-nodes-base.splitInBatches"
    ]
    if not loop_names:
        if feedback_edges:
            raise WorkflowError("Обнаружена обратная связь без узла Loop Over Items")
        return None
    if len(loop_names) != 1:
        raise WorkflowError("Пока поддерживается один узел Loop Over Items в workflow")

    name = loop_names[0]
    settings = generate_loop_node(compatible_node(nodes[name]))
    feedback = [edge for edge in feedback_edges if edge.target == name]
    if len(feedback) != 1 or feedback[0].input != 0 or feedback[0].output != 0:
        raise WorkflowError(
            f"Узел {name}: требуется ровно одна обратная связь с выхода 0 на вход 0"
        )
    feedback_edge = feedback[0]
    dag_edges = [edge for edge in main_edges if edge not in feedback_edges]
    loop_inputs = [edge for edge in dag_edges if edge.target == name]
    if len(loop_inputs) != 1 or loop_inputs[0].input != 0:
        raise WorkflowError(f"Узел {name}: требуется ровно один начальный вход")

    outgoing: dict[str, list[Any]] = {}
    incoming: dict[str, list[Any]] = {}
    for edge in dag_edges:
        outgoing.setdefault(edge.source, []).append(edge)
        incoming.setdefault(edge.target, []).append(edge)

    loop_edges = [edge for edge in dag_edges if edge.source == name and edge.output == settings.loop_output]
    if not loop_edges:
        raise WorkflowError(f"Узел {name}: выход loop не подключён")
    done_edges = (
        [edge for edge in dag_edges if edge.source == name and edge.output == settings.done_output]
        if settings.done_output is not None
        else []
    )

    def reachable(starts: list[Any]) -> set[str]:
        """Найти все узлы, достижимые из выбранного выхода Loop."""
        visited: set[str] = set()
        pending = [edge.target for edge in starts]
        while pending:
            current = pending.pop()
            if current == name or current in visited:
                continue
            visited.add(current)
            pending.extend(edge.target for edge in outgoing.get(current, []))
        return visited

    # Две ветви должны быть независимыми: тело запускается на каждой итерации,
    # а done-ветвь — только после завершения цикла.
    body_nodes = reachable(loop_edges)
    done_nodes = reachable(done_edges)
    overlap = body_nodes & done_nodes
    if overlap:
        raise WorkflowError(
            f"Узел {name}: ветви loop и done пересекаются у узла {sorted(overlap)[0]}"
        )

    for edge in dag_edges:
        if edge.target in body_nodes and edge.source not in body_nodes and not (
            edge.source == name and edge.output == settings.loop_output
        ):
            raise WorkflowError(
                f"Узел {edge.target}: вход в тело Loop должен идти из узла Loop или его тела"
            )
        if edge.target in done_nodes and edge.source not in done_nodes and not (
            edge.source == name and edge.output == settings.done_output
        ):
            raise WorkflowError(
                f"Узел {edge.target}: done-ветвь должна начинаться с выхода done узла {name}"
            )

    if feedback_edge.source not in body_nodes:
        raise WorkflowError(f"Узел {name}: обратная связь должна идти из тела цикла")

    before_nodes: set[str] = set()
    pending = [edge.source for edge in loop_inputs]
    while pending:
        current = pending.pop()
        if current == name or current in before_nodes:
            continue
        before_nodes.add(current)
        pending.extend(edge.source for edge in incoming.get(current, []))

    categorized = before_nodes | body_nodes | done_nodes | {name}
    unplaced = main_nodes - categorized
    if unplaced:
        raise WorkflowError(
            f"Узел {sorted(unplaced)[0]} не входит во вход, тело или done-ветвь Loop {name}"
        )
    if before_nodes & (body_nodes | done_nodes):
        raise WorkflowError(f"Узел {name}: путь до цикла пересекается с его выходами")

    order = (
        [node for node in base_order if node in before_nodes]
        + [name]
        + [node for node in base_order if node in body_nodes]
        + [node for node in base_order if node in done_nodes]
    )
    return {
        "name": name,
        "settings": settings,
        "feedback": feedback_edge,
        "body": body_nodes,
        "done": done_nodes,
        "order": order,
        "dag_edges": dag_edges,
    }


def _generate_supported_project(workflow: dict[str, Any]) -> dict[str, str]:
    """Скомпилировать workflow в набор файлов самостоятельного Python-проекта.

    Функция сначала проверяет структуру графа и поддерживаемость узлов, затем
    генерирует модуль каждого узла и собирает вызовы в ``main.py``. Все
    expressions разбираются на этом этапе; исходный JSON не нужен при запуске.

    Returns:
        Словарь ``относительный путь -> текст файла``. Его используют CLI,
        проверка синтаксиса и тесты generated-проекта.

    Raises:
        WorkflowError: если основная структура графа не может быть собрана.
    """
    if not workflow.get("nodes"):
        raise WorkflowError("Нельзя построить проект без узлов")

    # Разделяем вспомогательные AI-связи и обычные main-рёбра до сортировки графа.
    edges = connections(workflow)
    nodes = {node["name"]: node for node in workflow["nodes"]}
    if not nodes:
        raise WorkflowError("Workflow не содержит узлов")
    # AI-модель принадлежит агенту: запоминаем её имя, чтобы передать конфигурацию
    # прямо в модуль агента и исключить модель из последовательности узлов.
    models: dict[str, str] = {}
    auxiliary = [edge for edge in edges if edge.channel != "main"]
    for edge in auxiliary:
        if edge.channel != "ai_languageModel":
            raise WorkflowError(
                f"Узел {edge.target}: дополнительное соединение {edge.channel} не поддерживается"
            )
        if (
            edge.target_type != "ai_languageModel"
            or edge.output != 0
            or edge.input != 0
            or edge.target in models
            or nodes[edge.target]["type"] != "@n8n/n8n-nodes-langchain.agent"
        ):
            raise WorkflowError(
                f"Узел {edge.target}: неподдерживаемое соединение ai_languageModel "
                "(ожидается одно подключение к входу 0)"
            )
        models[edge.target] = edge.source
    model_names = set(models.values())
    if len(model_names) != len(models):
        agents = ", ".join(sorted(models))
        raise WorkflowError(f"Узлы {agents}: одна chat-модель не может быть подключена к нескольким AI Agent")
    all_main_edges = [edge for edge in edges if edge.channel == "main"]
    if any(edge.target_type != "main" or edge.source in model_names or edge.target in model_names or (edge.input != 0 and nodes[edge.target]["type"] != "n8n-nodes-base.merge") for edge in all_main_edges):
        raise WorkflowError("Неподдерживаемые основные соединения")
    main_nodes = set(nodes) - model_names
    # Обратное ребро Loop не участвует в топологической сортировке, но остаётся
    # в описании цикла для формирования реального for-блока в main.py.
    feedback_edges = loop_feedback_edges(workflow)
    main_edges = [edge for edge in all_main_edges if edge not in feedback_edges]
    base_order = [name for name in execution_order(workflow) if name not in model_names]
    loop_info = _loop_layout(nodes, main_nodes, all_main_edges, base_order, feedback_edges)
    if loop_info:
        main_edges = loop_info["dag_edges"]
    incoming = {name: 0 for name in main_nodes}
    outgoing = {name: 0 for name in main_nodes}
    for edge in main_edges:
        incoming[edge.target] += 1
    for edge in all_main_edges:
        outgoing[edge.source] += 1
    if sum(v == 0 for v in incoming.values()) != 1:
        raise WorkflowError("Workflow должен иметь ровно одну начальную точку")
    for source in main_nodes:
        ports = [edge.output for edge in main_edges if edge.source == source]
        if nodes[source]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.splitInBatches"} and any(port != 0 for port in ports):
            raise WorkflowError(f"Узел {source}: неподдерживаемые выходы")
        if nodes[source]["type"] == "n8n-nodes-base.splitInBatches":
            settings = generate_loop_node(compatible_node(nodes[source]))
            if any(port >= settings.output_count for port in ports):
                raise WorkflowError(f"Узел {source}: соединение с несуществующим выходом")
    order = loop_info["order"] if loop_info else base_order
    branching = any(
        nodes[name]["type"] in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.merge", "n8n-nodes-base.splitInBatches"}
        or outgoing[name] > 1 or incoming[name] > 1
        for name in main_nodes
    )
    linked_names = set().union(*(_linked_item_nodes(node.get("parameters", {})) for node in nodes.values()), set())
    linked_items = bool(linked_names)
    # Родители нужны для входных аргументов узлов и проверки доступности expressions.
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
    loop_state_name = None
    loop_body_call_start = None
    loop_body_order = [name for name in order if loop_info and name in loop_info["body"]]
    loop_body_nodes = loop_info["body"] if loop_info else set()
    converted = 0
    skipped = 0
    skipped_triggers: list[dict[str, Any]] = []
    dependencies: set[str] = set()
    completed: set[str] = set()
    ai_model_configs: dict[str, dict[str, Any]] = {}
    uses_gmail = False
    uses_smtp = False
    uses_email_send = False
    uses_wait = False
    email_smtp_credential = None
    http_credentials: set[str] = set()
    http_credential_descriptions: dict[str, set[str]] = {}
    postgres_credential = None
    uses_data_tables = False
    data_table_mappings: dict[str, str] = {}
    version_notes: list[tuple[str, str]] = []
    manual_fallback_notes: list[str] = []
    # Для каждого узла создаём отдельный модуль; ниже формируется его вызов в main.py.
    for name in order:
        node = nodes[name]
        kind = node["type"]
        conversion_node = compatible_node(node)
        version_warning = node_version_warning(node)
        manual_parameters = manual_parameter_payload(node) or None
        if version_warning:
            version_notes.append((name, version_warning))
        if manual_parameters and manual_parameters.get("unsupported_parameter_paths"):
            note = _manual_fallback_description(node, manual_parameters)
            if note not in manual_fallback_notes:
                manual_fallback_notes.append(note)
        # Оставляем в main.py отметку обо всех узлах, в том числе о пропущенных триггерах.
        node_comment = name.replace("\r", " ").replace("\n", " ")
        kind_comment = kind.replace("\r", " ").replace("\n", " ")
        calls.append(f"    # Узел n8n «{node_comment}» ({kind_comment})")
        trigger_name = kind.rsplit(".", 1)[-1].lower()
        if trigger_name.endswith("trigger") or kind == "n8n-nodes-base.webhook":
            skipped += 1
            skipped_triggers.append({
                "name": name,
                "type": kind,
                "parameters": node.get("parameters", {}),
            })
            if branching:
                calls.append(f"    {aliases[name]} = [items]")
            continue
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
        if kind not in {"n8n-nodes-base.httpRequest", "n8n-nodes-base.splitInBatches"} and node.get("retryOnFail", False) is not False:
            raise WorkflowError(f"Узел {name}: настройка retryOnFail пока не поддерживается")
        # Триггеры намеренно не запускаются: сгенерированный проект вызывается вручную.
        # Конвертер узла возвращает исходник функции и зависимости её параметров.
        stem = module_names[name]
        function = "run_node"
        try:
            if kind == "n8n-nodes-base.code":
                folder = "python_nodes"
                source = generate_code_node(conversion_node, function)
                arguments = "items"
            elif kind in {"n8n-nodes-base.convertToFile", "n8n-nodes-base.compression", "n8n-nodes-base.extractFromFile"}:
                folder = "file_nodes"
                settings = workflow.get("settings", {})
                timezone_name = settings.get("timezone") if isinstance(settings, dict) else None
                source = generate_file_node(conversion_node, function, timezone_name)
                dependencies.update(file_node_dependencies(node))
                arguments = "items"
            elif kind == "n8n-nodes-base.httpRequest":
                params = node.get("parameters", {})
                smtp_gmail = is_gmail_send_node(node)
                if smtp_gmail:
                    folder = "email_senders"
                    source, references = generate_smtp_node(conversion_node, function)
                    uses_smtp = True
                    dependencies.add("aiosmtplib>=3.0")
                else:
                    folder = "http_requests"
                    source = generate_http_node(conversion_node, function)
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
                source, references = generate_email_send_node(conversion_node, function)
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
                dependencies.add("aiosmtplib>=3.0")
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
                arguments = "items, results, execution"
            elif kind == "n8n-nodes-base.set":
                folder = "operations"
                source, references = generate_edit_fields(conversion_node, function)
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
                arguments = "items, results, execution"
            elif kind == "n8n-nodes-base.noOp":
                folder = "operations"
                source = generate_noop_node(conversion_node, function)
                arguments = "items"
            elif kind == "n8n-nodes-base.postgres":
                credential = node.get("credentials", {}).get("postgres", {})
                credential_id = credential.get("id") if isinstance(credential, dict) else None
                if not isinstance(credential_id, str) or not credential_id:
                    raise WorkflowError(f"Узел {name}: требуется PostgreSQL credential")
                if postgres_credential is not None and postgres_credential != credential_id:
                    raise WorkflowError(f"Узел {name}: несколько PostgreSQL credentials пока не поддерживаются")
                postgres_credential = credential_id
                folder = "database"
                source, references = generate_postgres_node(conversion_node, function)
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
                arguments = "items, results, execution"
                dependencies.add("psycopg[binary]>=3.1")
                files["database/postgres.py"] = POSTGRES_MODULE
            elif kind == "n8n-nodes-base.dataTable":
                folder = "database"
                uses_data_tables = True
                dependencies.add("asyncpg>=0.29")
                files["database/data_tables.py"] = DATA_TABLES_MODULE
                source, references, table_reference = generate_data_table_node(
                    conversion_node, function
                )
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(
                        f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}"
                    )
                arguments = "items, results, execution"
                if table_reference is not None:
                    mode, reference = table_reference
                    data_table_mappings.setdefault(
                        reference,
                        f"public.{_default_data_table_name(mode, reference)}",
                    )
            elif kind == "n8n-nodes-base.merge":
                folder = "operations"
                source, input_count = generate_merge_node(conversion_node, function)
                if [edge.input for edge in parents[name]] != list(range(input_count)):
                    raise WorkflowError(f"Узел {name}: Merge требует по одному соединению на каждый вход")
                arguments = "merge_inputs"
            elif kind == "n8n-nodes-base.splitInBatches":
                folder = "operations"
                source = LOOP_MODULE
                arguments = ""
            elif kind == "n8n-nodes-base.wait":
                folder = "operations"
                uses_wait = True
                source, references = generate_wait_node(conversion_node, function)
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(
                        f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}"
                    )
                arguments = "items, results, execution"
            elif kind == "@n8n/n8n-nodes-langchain.agent":
                if name not in models:
                    raise WorkflowError(f"Узел {name}: не подключена chat-модель через ai_languageModel")
                folder = "ai_nodes"
                model_name = models[name]
                model_prefix = module_names[model_name].upper()
                source, references, model_config = generate_ai_agent(
                    conversion_node, compatible_node(nodes[model_name]), function, model_prefix
                )
                ai_model_configs[model_prefix] = model_config
                missing = references - (completed & ancestors[name] if branching else completed)
                if missing:
                    raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
                arguments = "items, results, execution"
                dependencies.add("httpx>=0.27")
            elif kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
                folder = "operations"
                source, references, output_count = generate_condition_node(conversion_node, function)
                missing = references - (completed & ancestors[name])
                if missing:
                    raise WorkflowError(f"Узел {name}: результаты узлов ещё недоступны: {', '.join(sorted(missing))}")
                if any(edge.output >= output_count for edge in main_edges if edge.source == name):
                    raise WorkflowError(f"Узел {name}: соединение с несуществующим выходом")
                arguments = "items, results, execution"
            else:
                raise WorkflowError(f"Узел {name}: неподдерживаемый тип {kind}")
        except (WorkflowError, KeyError, TypeError, ValueError) as exc:
            fallback_layout = _MANUAL_FALLBACK_LAYOUTS.get(kind)
            if fallback_layout is None or not _can_generate_manual_fallback(node, exc):
                raise
            folder, arguments = fallback_layout
            manual_parameters = manual_parameter_payload(node, force=True)
            note = _manual_fallback_description(node, manual_parameters, exc)
            if note not in manual_fallback_notes:
                manual_fallback_notes.append(note)
            if kind == "n8n-nodes-base.httpRequest" and is_gmail_send_node(node):
                folder = "email_senders"
            source = _manual_version_fallback_source(
                node,
                function,
                asynchronous=True,
            )
            if kind in {"n8n-nodes-base.if", "n8n-nodes-base.switch"}:
                connected_ports = [edge.output for edge in main_edges if edge.source == name]
                output_count = max(connected_ports, default=1) + 1
        if version_warning and kind != "n8n-nodes-base.splitInBatches":
            source = add_manual_parameter_to_source(source, function, version_warning)
        if kind != "n8n-nodes-base.splitInBatches" and not re.search(
            rf"(?m)^async def {re.escape(function)}\(", source
        ):
            raise WorkflowError(
                f"Узел {name}: конвертер должен генерировать async-функцию {function}"
            )
        call_arguments = arguments
        if manual_parameters is not None:
            call_arguments += f", n8n_manual_parameters={manual_parameters!r}"
        invocation = (
            f"await _run_logged_node({name!r}, "
            f"lambda: {stem}({call_arguments}))"
        )
        if manual_parameters and manual_parameters.get("unsupported_parameter_paths"):
            calls.append(f"    # TODO: {_manual_fallback_description(node, manual_parameters)}")
        # Сохраняем исходник модуля и регистрируем импорт для явного вызова из main.py.
        files[f"{folder}/{stem}.py"] = source
        files[f"{folder}/__init__.py"] = ""
        if kind == "n8n-nodes-base.splitInBatches":
            files.pop(f"{folder}/{stem}.py", None)
            files["operations/loop.py"] = LOOP_MODULE
            if "from operations.loop import Loop" not in imports:
                imports.append("from operations.loop import Loop")
            loop_state_name = f"loop_{stem}"
            loop_settings = loop_info["settings"]
            calls.append("    items = []")
            for edge in parents[name]:
                value = f"{aliases[edge.source]}[{edge.output}]"
                fanout = sum(1 for item in main_edges if item.source == edge.source and item.output == edge.output) > 1
                calls.append(f"    items.extend({'deepcopy(' + value + ')' if fanout else value})")
            calls.append(
                f"    logging.info({('Узел «' + name + '»: начат; входных элементов: %d')!r}, len(items))"
            )
            calls.extend([
                # Экземпляр хранит настройки пакетов и выполнения конкретного Loop.
                (
                    f"    {loop_state_name} = Loop("
                    f"batch_size={loop_settings.batch_size}, reset={loop_settings.reset!r}"
                    f"{', execute_once=True' if loop_settings.execute_once else ''}"
                    f"{', retry_on_fail=True' if loop_settings.retry_on_fail else ''}"
                    f"{', max_tries=' + repr(loop_settings.max_tries) if loop_settings.retry_on_fail else ''}"
                    f"{', wait_between_tries=' + repr(loop_settings.wait_between_tries) if loop_settings.retry_on_fail else ''}"
                    f"{', n8n_manual_parameters=' + repr(manual_parameters) if manual_parameters is not None else ''}"
                    ")"
                ),
                f"    {aliases[name]} = [[] for _ in range({loop_settings.output_count})]",
                "    try:",
                f"        async for loop_batch in {loop_state_name}.iterate(items):",
            ])
            if linked_items:
                calls.append(f"            {loop_state_name}.link_batch({name!r}, loop_batch)")
            calls.extend([
                f"            {aliases[name]}[{loop_settings.loop_output}] = loop_batch",
                f"            results[{name!r}] = loop_batch",
            ])
            loop_body_call_start = len(calls)
            converted += 1
            completed.add(name)
            continue
        else:
            imports.append(f"from {folder}.{stem} import {function} as {stem}")
        # Для графа с ветвлениями сохраняем выходы каждого узла отдельно;
        # линейному workflow достаточно переменной items.
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
            calls.extend([
                f"    {aliases[name]} = [[] for _ in range({count})]",
                f"    if {ready}:",
            ])
            if node.get("executeOnce", False):
                calls.append("        items = items[:1]")
            if is_condition:
                if linked_items:
                    calls.extend([
                        f"        branch_outputs = {invocation}",
                        f"        {aliases[name]} = _link_outputs(items, branch_outputs, {name!r}, "
                        f"len({loop_state_name}.results.get({name!r}, [])))" if name in loop_body_nodes else
                        f"        {aliases[name]} = _link_outputs(items, branch_outputs, {name!r})",
                    ])
                else:
                    calls.append(f"        {aliases[name]} = {invocation}")
            else:
                if linked_items and kind != "n8n-nodes-base.merge":
                    calls.append("        source_items = items")
                calls.append(f"        items = {invocation}")
                if node.get("alwaysOutputData", False):
                    calls.extend(['        if not items:', '            items = [{"json": {}}]'])
                if linked_items:
                    if kind == "n8n-nodes-base.merge":
                        calls.append(
                            f"        items = _link_merge(merge_inputs, items, {name!r}, "
                            f"len({loop_state_name}.results.get({name!r}, [])))"
                            if name in loop_body_nodes else
                            f"        items = _link_merge(merge_inputs, items, {name!r})"
                        )
                    else:
                        calls.append(
                            f"        items = _link_lineage(source_items, items, {name!r}, "
                            f"len({loop_state_name}.results.get({name!r}, [])))"
                            if name in loop_body_nodes else
                            f"        items = _link_lineage(source_items, items, {name!r})"
                        )
                if kind == "n8n-nodes-base.dataTable":
                    calls.append("        for output_item in items: output_item.pop('pairedItem', None)")
                stored_items = (
                    f"{loop_state_name}.record_result({name!r}, items)"
                    if name in loop_body_nodes else "items"
                )
                calls.extend([f"        {aliases[name]}[0] = items", f"        results[{name!r}] = {stored_items}"])
            calls.extend([
                "    else:",
                f"        logging.info({('Узел «' + name + '»: пропущен — нет входных данных')!r})",
            ])
            if is_condition:
                flattened = f"[item for output in {aliases[name]} for item in output]"
                stored_outputs = (
                    f"{loop_state_name}.record_result({name!r}, {flattened})"
                    if name in loop_body_nodes else flattened
                )
                calls.append(f"    results[{name!r}] = {stored_outputs}")
        else:
            calls.extend([
                "    if not items:",
                f"        logging.info({('Узел «' + name + '»: пропущен — нет входных данных')!r})",
                "        return []",
            ])
            if node.get("executeOnce", False):
                calls.append("    items = items[:1]")
            if linked_items:
                calls.append("    source_items = items")
            calls.append(f"    items = {invocation}")
            if node.get("alwaysOutputData", False):
                calls.extend(['    if not items:', '        items = [{"json": {}}]'])
            if linked_items:
                calls.append(
                    f"    items = _link_lineage(source_items, items, {name!r}, "
                    f"len({loop_state_name}.results.get({name!r}, [])))"
                    if name in loop_body_nodes else
                    f"    items = _link_lineage(source_items, items, {name!r})"
                )
            if kind == "n8n-nodes-base.dataTable":
                calls.append("    for output_item in items: output_item.pop('pairedItem', None)")
            stored_items = (
                f"{loop_state_name}.record_result({name!r}, items)"
                if name in loop_body_nodes else "items"
            )
            calls.append(f"    results[{name!r}] = {stored_items}")
        converted += 1
        completed.add(name)
        if loop_info and name in loop_info["body"] and name == loop_body_order[-1]:
            for call_index in range(loop_body_call_start, len(calls)):
                calls[call_index] = "        " + calls[call_index]
            feedback = loop_info["feedback"]
            calls.append(
                f"            await {loop_state_name}.collect({aliases[feedback.source]}[{feedback.output}])"
            )
            calls.extend([
                "    except Exception as exc:",
                f"        logging.error({('Узел «' + loop_info['name'] + '»: цикл прерван: %s')!r}, exc)",
                "        raise",
            ])
            loop_settings = loop_info["settings"]
            if loop_settings.done_output is not None:
                calls.append(
                    f"    {aliases[loop_info['name']]}[{loop_settings.done_output}] = "
                    f"deepcopy({loop_state_name}.done_items)"
                )
            calls.append(
                f"    results[{loop_info['name']!r}] = {loop_state_name}.done_items"
            )
            calls.append(
                f"    logging.info({('Узел «' + loop_info['name'] + '»: завершён; выходных элементов: %d')!r}, len({loop_state_name}.done_items))"
            )
    if not converted:
        raise WorkflowError("Workflow не содержит исполняемых поддерживаемых узлов")
    terminal = [name for name in order if outgoing[name] == 0 and nodes[name]["type"] not in {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.manualTrigger"}]
    if loop_info:
        terminal_values = [
            f"{loop_state_name}.results.get({name!r}, [])" if name in loop_body_nodes
            else f"{aliases[name]}[0]"
            for name in terminal
        ]
        return_code = " + ".join(terminal_values) or f"{loop_state_name}.done_items"
    else:
        return_code = ' + '.join(f'{aliases[name]}[0]' for name in terminal) or '[]' if branching else 'items'
    lineage_helpers = ('LINKED_ITEM_NODES = ' + repr(tuple(sorted(linked_names))) + '\n\n\n' + '''def _link_lineage(source_items, output_items, node_name, index_offset=0):
    """Передать выходным items связь с соответствующими входными items.

    pairedItem в результате имеет приоритет. Если его нет, связь определяется
    только для однозначных случаев: один вход или одинаковое число входов и выходов.
    """
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
        lineage[node_name] = index_offset + output_index
        output_item["_n8n_lineage"] = lineage
    return output_items


def _link_outputs(source_items, outputs, node_name, index_offset=0):
    """Сохранить paired-item lineage при распределении items по портам IF/Switch.

    Выходы нумеруются в общем порядке, а ``index_offset`` учитывает прошлые
    итерации Loop, чтобы ссылки ``.item`` указывали на правильный элемент.
    """
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
            lineage[node_name] = index_offset + len(flattened)
            output_item["_n8n_lineage"] = lineage
            linked_outputs[port].append(output_item)
            flattened.append(output_item)
    return linked_outputs


def _tag_merge_inputs(inputs):
    """Временно отметить источник каждого item перед выполнением Merge.

    Метки нужны только внутри generated main.py: конвертер Merge возвращает их
    вместе с результатом, чтобы затем восстановить родительские ссылки.
    """
    for input_index, items in enumerate(inputs):
        for item_index, item in enumerate(items):
            item["__transcriper_input"] = input_index
            item["__transcriper_index"] = item_index
    return inputs


def _link_merge(inputs, output_items, node_name, index_offset=0):
    """Восстановить родительские связи для результатов Merge.

    Если объединены разные items одного предка, его paired-item связь удаляется.
    Когда на такой неоднозначный предок ссылается expression ``.item``, выполнение
    останавливается с ошибкой вместо выбора произвольного элемента.
    """
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
        lineage[node_name] = index_offset + output_index
        item["_n8n_lineage"] = lineage
    return output_items


def _clean_items(items):
    """Удалить внутренние индексы, не предназначенные вызывающему коду."""
    for item in items:
        item.pop("_n8n_lineage", None)
        item.pop("__transcriper_input", None)
        item.pop("__transcriper_index", None)
        item.pop("__transcriper_sources", None)
    return items
''') if linked_items else ""
    runner_name = "run_async"
    runner_definition = "async def"
    sync_wrapper = (
        "\n\ndef run(input_data=None):\n"
        "    \"\"\"Синхронная обёртка для ручного запуска асинхронного workflow.\"\"\"\n"
        "    if sys.platform == \"win32\":\n"
        "        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:\n"
        "            return runner.run(run_async(input_data))\n"
        "    return asyncio.run(run_async(input_data))\n"
    )
    files["main.py"] = (
        '"""Точка ручного запуска сгенерированного workflow."""\n\n'
        + "import asyncio\nimport logging\nimport sys\nimport uuid\n"
        + ("from copy import deepcopy\n" if branching else "")
        + "\n"
        + "\n".join(imports)
        + "\n\n\n"
        + lineage_helpers
        + '''

async def _run_logged_node(node_name, operation):
    """Выполнить узел, записать ход выполнения и сохранить контекст ошибки."""
    logging.info("Узел «%s»: начат", node_name)
    try:
        result = await operation()
    except Exception:
        logging.exception("Узел «%s»: ошибка выполнения", node_name)
        raise
    if isinstance(result, list) and result and all(isinstance(port, list) for port in result):
        item_count = sum(len(port) for port in result)
    else:
        item_count = len(result) if isinstance(result, list) else int(result is not None)
    logging.info("Узел «%s»: завершён; выходных элементов: %d", node_name, item_count)
    return result
'''
        + f'\n\n{runner_definition} {runner_name}(input_data=None):\n'
        + '    """Вручную выполнить сгенерированную цепочку узлов.\n\n'
        + '    Args:\n'
        + '        input_data: Необязательный список n8n items вида {"json": {...}}.\n'
        + '            Если аргумент не задан, workflow получает один пустой item.\n\n'
        + '    Returns:\n'
        + '        Items терминальных узлов с удалёнными служебными полями lineage.\n'
        + '    """\n'
        + '    items = input_data if input_data is not None else [{"json": {}}]\n'
        + '    results = {}\n'
        + '    execution = {"id": str(uuid.uuid4())}\n'
        + f'    logging.info("Workflow «%s»: запуск; идентификатор: %s", {str(workflow.get("name") or "без имени")!r}, execution["id"])\n'
        + "\n".join(calls)
        + f'\n    result = {"_clean_items(" + return_code + ")" if linked_items else return_code}\n'
        + '    logging.info("Workflow завершён; выходных элементов: %d", len(result))\n'
        + '    return result\n'
        + sync_wrapper
        + '\n\nif __name__ == "__main__":\n'
        + '    if hasattr(sys.stdout, "reconfigure"):\n'
        + '        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")\n'
        + '    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)\n'
        + '    try:\n'
        + '        run()\n'
        + '    except Exception as exc:\n'
        + '        logging.error("Workflow остановлен с ошибкой: %s", exc)\n'
        + '        raise SystemExit(1) from None\n'
    )
    files["run.bat"] = (
        "@echo off\n"
        "chcp 65001 >nul\n"
        "setlocal\n"
        "set PYTHONUTF8=1\n"
        'cd /d "%~dp0"\n'
        "uv run python main.py\n"
        'set "workflow_exit_code=%ERRORLEVEL%"\n'
        'if not "%workflow_exit_code%"=="0" echo Ошибка выполнения workflow. Код завершения: %workflow_exit_code%.\n'
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
        '    printf "\\nСценарий завершён с кодом выхода %s.\\n" "$workflow_exit_code"\n'
        '    printf "Нажмите Enter, чтобы закрыть терминал..."\n'
        '    IFS= read -r _\n'
        "fi\n"
        'exit "$workflow_exit_code"\n'
    )
    config = '"""Настройки авторизации generated-проекта.\n\nКаждое значение читается из переменной окружения; реальные секреты не хранятся\nв workflow и не записываются в исходники конвертера.\n"""\n\nimport os\n'
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
    if postgres_credential is not None or uses_data_tables:
        config += ('\n# Подключение к PostgreSQL; пароль не хранится в исходном workflow.\n'
                   'POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")\n'
                   'POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))\n'
                   'POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "")\n'
                   'POSTGRES_USER = os.getenv("POSTGRES_USER", "")\n'
                   'POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")\n'
                   'POSTGRES_CONNECT_TIMEOUT = float(os.getenv("POSTGRES_CONNECT_TIMEOUT", "30"))\n')
    if uses_data_tables:
        config += (
            '\n# Сопоставление ссылок n8n Data Table с именами таблиц PostgreSQL.\n'
            f'DATA_TABLES = {data_table_mappings!r}\n'
        )
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
    if postgres_credential is not None or uses_data_tables:
        required.extend(["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DATABASE", "POSTGRES_USER", "POSTGRES_PASSWORD"])
    credentials = ', '.join(f'`{key}`' for key in required) if required else 'не требуются'
    data_table_documentation = ""
    if uses_data_tables:
        data_table_documentation = (
            "## Data Tables\n\n"
            "Операции строк Data Table выполняются через асинхронный asyncpg. "
            "Укажите подключение в POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, "
            "POSTGRES_USER и POSTGRES_PASSWORD. "
            "Ссылки n8n на таблицы сопоставляются с именами PostgreSQL через словарь DATA_TABLES "
            "в config.py; проверьте это сопоставление и заранее создайте соответствующие таблицы "
            "со столбцами, используемыми workflow. Таблице нужны системные столбцы id, createdAt "
            "и updatedAt; id должен генерироваться PostgreSQL, даты должны иметь тип TIMESTAMPTZ. "
            "Пример: id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
            '"createdAt" TIMESTAMPTZ NOT NULL, "updatedAt" TIMESTAMPTZ NOT NULL. '
            "При insert/upsert даты устанавливаются автоматически, updatedAt также обновляется при update/upsert. "
            "Данные внутренних таблиц n8n автоматически в PostgreSQL проекта не копируются.\n\n"
            "Каждая найденная строка возвращается отдельным item в форме "
            '{"json": {"id": "...", "createdAt": "...", "updatedAt": "...", "...": "..."}}. '
            "Даты и время переводятся в ISO-строки. Встраивайте проект через "
            "асинхронную функцию `await run_async(...)`.\n\n"
        )
    wait_documentation = ""
    if uses_wait:
        wait_documentation = (
            "## Wait\n\n"
            "Узел Wait является асинхронным: режим `timeInterval` использует `amount` и `unit` "
            "(seconds, minutes, hours или days), а `specificTime` ждёт до `dateTime`. "
            "После паузы передаются те же входные items. Режимы `webhook` и `form` пропускаются "
            "с диагностикой, так как standalone-проект не хранит приостановленный запуск и не "
            "поднимает endpoint для внешнего возобновления.\n\n"
        )
    trigger_documentation = ""
    if skipped_triggers:
        trigger_sections = []
        for trigger in skipped_triggers:
            trigger_name = trigger["name"].replace("\r", " ").replace("\n", " ").replace("`", "\\`")
            trigger_type = trigger["type"].replace("\r", " ").replace("\n", " ").replace("`", "\\`")
            parameters = json.dumps(trigger["parameters"], ensure_ascii=False, indent=2)
            fence = "```"
            while fence in parameters:
                fence += "`"
            trigger_sections.append(
                f"### {trigger_name}\n\n"
                f"- Тип n8n: `{trigger_type}`\n"
                "- Параметры:\n\n"
                f"{fence}json\n{parameters}\n{fence}\n"
            )
        trigger_documentation = (
            "## Триггеры исходного workflow\n\n"
            "Триггеры из JSON приведены ниже для справки. Они не исполняются; "
            "сгенерированный workflow запускается вручную через `main.py`.\n\n"
            + "\n".join(trigger_sections)
            + "\n"
        )
    version_documentation = ""
    if version_notes:
        version_documentation = (
            "## Предупреждения о версиях узлов\n\n"
            "Для перечисленных узлов использована ближайшая версия, которую умеет преобразовывать конвертер. "
            "В каждом generated-модуле есть TODO-комментарий и аргумент `n8n_manual_parameters` с шаблоном структуры настроек. "
            "Значения параметров заменены на `None`, чтобы не раскрывать секреты; заполните их и реализуйте отличающиеся настройки вручную. "
            "Если схема узла не распознана конвертером, его generated-функция завершится с `NotImplementedError`, пока вы не добавите реализацию. "
            "Проверьте настройки и вручную добавьте поведение, отличающееся в исходной версии.\n\n"
            + "\n".join(f"- {message}" for _, message in version_notes)
            + "\n\n"
        )
    manual_fallback_documentation = ""
    if manual_fallback_notes:
        manual_fallback_documentation = (
            "## Узлы для ручной доработки\n\n"
            "Эти узлы включены в проект как TODO-заготовки. Их вызовы сохранены в `main.py`, "
            "а запуск остановится с `NotImplementedError`, пока не будет реализована неизвестная часть. "
            "Пути перечисляют только имена настроек; значения workflow и секреты не копируются.\n\n"
            + "\n".join(f"- {note}" for note in manual_fallback_notes)
            + "\n\n"
        )
    files["README.md"] = (
        '# Сгенерированный проект\n\n'
        'Проект запускает преобразованный workflow как обычную Python-программу. Исходный JSON для запуска не нужен.\n\n'
        + trigger_documentation
        + version_documentation
        + manual_fallback_documentation
        + data_table_documentation
        + wait_documentation
        + '## Требования и установка\n\n'
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
        '`main.py` можно импортировать из другого async-кода: `from main import run_async`, затем `await run_async(input_data)`. '
        'Для обычного ручного запуска доступна синхронная обёртка `run(input_data)`, которая создаёт event loop. '
        'Каждый узел сгенерирован как async-функция с нативными async-клиентами для сетевых и базовых операций.\n'
    )
    files[".gitignore"] = '.env\n.env.*\n!.env.example\n__pycache__/\n*.pyc\n.venv/\n'
    version_report = (
        "\n\nПредупреждения о версиях:\n"
        + "\n".join(f"- {message}" for _, message in version_notes)
        if version_notes else ""
    )
    manual_fallback_report = (
        "\n\nУзлы для ручной доработки:\n"
        + "\n".join(f"- {note}" for note in manual_fallback_notes)
        if manual_fallback_notes else ""
    )
    files["conversion_report.md"] = (f'# Отчёт о конвертации\n\nУзлов: {len(nodes)}. '
        f'Преобразовано: {converted}. Моделей подключено: {len(model_names)}. Триггеров пропущено: {skipped}.\n\n'
        f'Настройки авторизации: {credentials}.' + version_report + manual_fallback_report + '\n')
    return files


def _empty_partial_project() -> dict[str, str]:
    """Создать структуру проекта, если пропущены все исполняемые узлы."""
    sample = {
        "nodes": [{
            "id": "partial-conversion-placeholder",
            "name": "Заглушка частичной конвертации",
            "type": "n8n-nodes-base.noOp",
            "parameters": {},
        }],
        "connections": {},
    }
    files = _generate_supported_project(sample)
    files = {
        path: content for path, content in files.items()
        if not path.startswith("operations/")
    }
    files["main.py"] = '''"""Точка входа частично сконвертированного workflow."""

import asyncio
import logging
import sys


async def run_async(input_data=None):
    """Асинхронно вернуть входные items, если все узлы были пропущены."""
    items = input_data if input_data is not None else [{"json": {}}]
    logging.info("Все исполняемые узлы пропущены; возвращаю исходные items: %d", len(items))
    return items


def run(input_data=None):
    """Запустить async workflow из обычного синхронного кода."""
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(run_async(input_data))
    return asyncio.run(run_async(input_data))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)
    run()
'''
    files["conversion_report.md"] = (
        "# Отчёт о конвертации\n\nПреобразовано: 0. Все исполняемые узлы пропущены.\n"
    )
    return files


def _node_named_in_error(workflow: dict[str, Any], error: str) -> str | None:
    """Найти узел, имя которого указано в диагностике конвертации."""
    nodes = [
        node for node in workflow.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    ]
    match = re.search(r"(?:Узел|Node)\s+(.+?)(?::|,|$)", error, re.IGNORECASE)
    if match:
        named = match.group(1).strip().strip("'\"«»")
        if any(node["name"] == named for node in nodes):
            return named
    matches = [node["name"] for node in nodes if node["name"] and node["name"] in error]
    return max(matches, key=len) if len(matches) == 1 else None


def _is_trigger_node(node: dict[str, Any]) -> bool:
    """Определить, исключает ли n8n этот узел из исполнения как триггер."""
    kind = node.get("type", "")
    short_name = kind.rsplit(".", 1)[-1].lower() if isinstance(kind, str) else ""
    return short_name.endswith("trigger") or kind == "n8n-nodes-base.webhook"


def _update_partial_conversion_report(
    files: dict[str, str],
    original_nodes: Any,
    original_count: int,
    skipped_parts: list[tuple[str, str, str]],
) -> None:
    """Пересчитать итоговый отчёт по исходному workflow, включая пропущенные узлы."""
    report = files.get("conversion_report.md", "")
    summary = re.search(
        r"Узлов: \d+\. Преобразовано: (\d+)\. Моделей подключено: (\d+)\. Триггеров пропущено: (\d+)\.",
        report,
    )
    converted = int(summary.group(1)) if summary else 0
    model_count = int(summary.group(2)) if summary else 0
    trigger_count = sum(
        _is_trigger_node(node)
        for node in original_nodes
        if isinstance(node, dict)
    ) if isinstance(original_nodes, list) else 0
    source_summary = (
        f"Узлов в исходном workflow: {original_count}. Преобразовано: {converted}. "
        f"Моделей подключено: {model_count}. Триггеров пропущено: {trigger_count}. "
        f"Частей с комментариями: {len(skipped_parts)}."
    )
    if summary:
        report = report[:summary.start()] + source_summary + report[summary.end():]
    elif report:
        report = report.replace("Преобразовано: 0. Все исполняемые узлы пропущены.", source_summary, 1)
    else:
        report = f"# Отчёт о конвертации\n\n{source_summary}\n"
    files["conversion_report.md"] = report


def generate_project(
    workflow: dict[str, Any],
    output: Path | None = None,
    *,
    langgraph: bool = False,
) -> dict[str, str]:
    """Создать проект, отметить и пропустить части workflow, которые нельзя перенести.

    При ошибке конвертации узла он исключается, а оставшийся граф собирается
    повторно. Если ошибка относится ко всему графу и имя узла неизвестно,
    генератор по очереди проверяет исключение оставшихся узлов. Даже если
    собрать подграф не удалось, возвращаются каркас проекта и подробный отчёт.
    """
    original = copy.deepcopy(workflow)
    original_nodes = original.get("nodes", []) if isinstance(original, dict) else []
    original_count = len(original_nodes) if isinstance(original_nodes, list) else 0
    if original_count == 0:
        raise WorkflowError("Workflow не содержит узлов")

    forced_skips: dict[str, str] = {}
    candidate, skipped_parts = _prepare_partial_workflow(
        original, forced_skips, langgraph=langgraph
    )
    files: dict[str, str] | None = None
    last_error: str | None = None

    while candidate.get("nodes"):
        try:
            conversion_input = (
                _prepare_langgraph_conversion_input(candidate)
                if langgraph
                else candidate
            )
            files = _generate_supported_project(conversion_input)
            if langgraph:
                files = _apply_langgraph_mode(candidate, files)
            break
        except Exception as exc:
            last_error = str(exc) or f"{type(exc).__name__}: ошибка конвертера"
            culprit = _node_named_in_error(candidate, last_error)
            if culprit is not None and culprit not in forced_skips:
                forced_skips[culprit] = f"Ошибка конвертации узла: {last_error}"
                candidate, skipped_parts = _prepare_partial_workflow(
                    original, forced_skips, langgraph=langgraph
                )
                continue

            # Диагностика графа может не указывать конкретный исходный узел.
            # Пробуем исключить один узел, чтобы собрать оставшуюся часть графа.
            for node in candidate.get("nodes", []):
                if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                    continue
                name = node["name"]
                if name in forced_skips or _is_trigger_node(node):
                    continue
                trial_skips = dict(forced_skips)
                trial_skips[name] = (
                    f"Общая схема workflow не собралась ({last_error}); узел пропущен."
                )
                trial, trial_notes = _prepare_partial_workflow(
                    original, trial_skips, langgraph=langgraph
                )
                if not trial.get("nodes"):
                    continue
                try:
                    conversion_input = (
                        _prepare_langgraph_conversion_input(trial)
                        if langgraph
                        else trial
                    )
                    trial_files = _generate_supported_project(conversion_input)
                    if langgraph:
                        trial_files = _apply_langgraph_mode(trial, trial_files)
                except Exception:
                    continue
                forced_skips = trial_skips
                candidate, skipped_parts, files = trial, trial_notes, trial_files
                break
            if files is not None:
                break

            # Исключение одного узла не исправило граф. Сохраняем результат
            # конвертации и объясняем, почему остальная часть пропущена.
            for node in candidate.get("nodes", []):
                if isinstance(node, dict) and isinstance(node.get("name"), str):
                    forced_skips.setdefault(
                        node["name"],
                        f"Граф workflow не удалось собрать ({last_error}); узел не включён в проект.",
                    )
            candidate, skipped_parts = _prepare_partial_workflow(
                original, forced_skips, langgraph=langgraph
            )
            break

    if files is None:
        files = _empty_partial_project()
        if langgraph:
            files = _apply_langgraph_mode(candidate, files)
    _update_partial_conversion_report(files, original_nodes, original_count, skipped_parts)
    _append_skipped_part_notes(files, skipped_parts)
    _write_project_files(files, output)
    return files


def _prepare_langgraph_conversion_input(workflow: dict[str, Any]) -> dict[str, Any]:
    """Временно упростить настройки найденного AI HTTP-запроса для общей проверки.

    Обычный HTTP-генератор продолжает обрабатывать остальные запросы workflow.
    Выражение JSON-тела AI-узла не должно отклоняться только потому, что оно
    выходит за пределы подмножества выражений обычного конвертера. После общей
    проверки исходный узел передаётся генератору агента LangGraph.
    """
    from n8n_transcriper.langgraph_mode.detection import detect_ai_request

    candidate = copy.deepcopy(workflow)
    original_nodes = {
        node.get("name"): node
        for node in workflow.get("nodes", [])
        if isinstance(node, dict)
    }
    for node in candidate.get("nodes", []):
        original = original_nodes.get(node.get("name"))
        if not isinstance(original, dict) or not detect_ai_request(original).is_ai:
            continue
        source_parameters = original.get("parameters", {})
        parameters = {
            "method": "POST",
            "url": source_parameters.get("url", ""),
            "sendBody": True,
            "contentType": "json",
            "specifyBody": "json",
            "jsonBody": (
                '{"model":"langgraph-validation",'
                '"messages":[{"role":"user","content":"validation"}],'
                '"temperature":0}'
            ),
            "options": {},
        }
        for key in ("authentication", "genericAuthType"):
            if key in source_parameters:
                parameters[key] = copy.deepcopy(source_parameters[key])
        headers = source_parameters.get("headerParameters", {})
        entries = headers.get("parameters", []) if isinstance(headers, dict) else []
        if isinstance(entries, list) and entries:
            safe_entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                safe_entry = copy.deepcopy(entry)
                value = safe_entry.get("value")
                if isinstance(value, str) and (value.startswith("=") or "{{" in value):
                    safe_entry["value"] = ""
                safe_entries.append(safe_entry)
            parameters["sendHeaders"] = True
            parameters["headerParameters"] = {"parameters": safe_entries}
        node["parameters"] = parameters
        # Повторы для запроса к провайдеру выполняет сгенерированный адаптер.
        node["retryOnFail"] = False
    return candidate


def _apply_langgraph_mode(
    workflow: dict[str, Any], files: dict[str, str]
) -> dict[str, str]:
    """Добавить runner LangGraph, не меняя генерацию в обычном режиме."""
    from n8n_transcriper.langgraph_mode.agent_generation import (
        generate_http_agent_node,
        generate_n8n_agent_node,
    )
    from n8n_transcriper.langgraph_mode.detection import detect_ai_request
    from n8n_transcriper.langgraph_mode.renderer import render_langgraph_main

    main_source = files.get("main.py")
    if not main_source or not workflow.get("nodes"):
        return files
    node_map = {
        node["name"]: node
        for node in workflow.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }
    module_names = _module_names(node_map)
    edges = connections(workflow)
    model_names = {
        edge.source
        for edge in edges
        if edge.channel == "ai_languageModel"
        and edge.target in node_map
        and node_map[edge.target].get("type") == "@n8n/n8n-nodes-langchain.agent"
    }
    main_nodes = set(node_map) - model_names
    all_main_edges = [edge for edge in edges if edge.channel == "main"]
    feedback_edges = loop_feedback_edges(workflow)
    base_order = [
        name for name in execution_order(workflow) if name not in model_names
    ]
    loop_info = _loop_layout(
        node_map, main_nodes, all_main_edges, base_order, feedback_edges
    )
    model_sources = {
        edge.target: edge.source
        for edge in edges
        if edge.channel == "ai_languageModel"
        and edge.target in node_map
        and node_map[edge.target].get("type") == "@n8n/n8n-nodes-langchain.agent"
    }
    agent_nodes: set[str] = set()
    ai_providers: set[str] = set()
    config_additions: list[str] = []
    ai_dependencies: set[str] = set()
    for name, node in node_map.items():
        if node.get("type") == "n8n-nodes-base.httpRequest":
            detection = detect_ai_request(node)
            if not detection.is_ai:
                continue
            source, config_block, _ = generate_http_agent_node(
                node, detection, module_names[name]
            )
            files.pop(f"http_requests/{module_names[name]}.py", None)
            files[f"ai_nodes/{module_names[name]}.py"] = source
            files["ai_nodes/__init__.py"] = ""
            config_additions.append(config_block)
            agent_nodes.add(name)
            ai_providers.add(detection.provider or "openai_compatible")
            if detection.provider == "gemini" and detection.operation == "interactions":
                ai_dependencies.add("google-genai>=2.3.0")
            elif detection.provider == "openai_compatible" and detection.endpoint is None:
                ai_dependencies.update({"langchain>=1.0", "httpx>=0.27"})
            else:
                ai_dependencies.update({
                    "langchain>=1.0",
                    "langchain-" + _langchain_package(detection.provider),
                })
        elif node.get("type") == "@n8n/n8n-nodes-langchain.agent":
            model_name = model_sources.get(name)
            if model_name not in node_map:
                continue
            source, _, dependency = generate_n8n_agent_node(
                node,
                node_map[model_name],
                module_names[name],
                module_names[model_name].upper(),
            )
            files[f"ai_nodes/{module_names[name]}.py"] = source
            agent_nodes.add(name)
            ai_dependencies.update({"langchain>=1.0", dependency})
            ai_providers.add(_langchain_provider_for_model(node_map[model_name]))
    if config_additions:
        files["config.py"] = files.get("config.py", "import os\n") + "".join(config_additions)
    if not any(
        path.startswith("http_requests/") and path != "http_requests/__init__.py"
        for path in files
    ):
        files.pop("http_requests/__init__.py", None)
    files["main.py"] = render_langgraph_main(
        workflow,
        module_names,
        loop_info=loop_info,
        agent_nodes=agent_nodes,
    )

    dependencies = {
        line.strip()
        for line in files.get("requirements.txt", "").splitlines()
        if line.strip()
    }
    dependencies.add("langgraph>=1.0")
    dependencies.update(ai_dependencies)
    if not any(
        re.search(r"^\s*(?:import\s+httpx|from\s+httpx\s+import)\b", source, re.MULTILINE)
        for path, source in files.items()
        if path.endswith(".py")
    ):
        dependencies.discard("httpx>=0.27")
    files["requirements.txt"] = "".join(
        f"{dependency}\n" for dependency in sorted(dependencies)
    )
    files["pyproject.toml"] = _project_toml(dependencies)
    readme = files.get("README.md", "")
    files["README.md"] = (
        readme
        + "\n## LangGraph\n\n"
        + "Используйте флаг `--langgraph`, чтобы запускать workflow через LangGraph. "
        + "Каждый поддерживаемый узел становится отдельным шагом графа. Тело `Loop Over Items` "
        + "создаётся как вложенный граф, а узлы до и после цикла остаются шагами основного графа.\n"
    )
    if ai_providers:
        provider_names = ", ".join(f"`{provider}`" for provider in sorted(ai_providers))
        files["README.md"] += (
            "\n### AI-узлы в LangGraph\n\n"
            "Распознавание AI-вызовов в HTTP Request включается только с `--langgraph`. Детектор учитывает адрес сервера, путь endpoint и структуру JSON-тела; одного поля `messages` без модели, ролей и параметров генерации недостаточно.\n\n"
            "Известные адреса API: OpenAI и DeepSeek `/v1/chat/completions`, OpenRouter `/api/v1/chat/completions`, Anthropic `/v1/messages`, Gemini `:generateContent` и `/v1/interactions`, Ollama `/api/chat` и `/api/generate`. Для собственного адреса требуется распознаваемая структура модели, например `model`, сообщения с полями `role` и `content`, а также параметры генерации.\n\n"
            "Для внутренних endpoint с распознанной OpenAI-compatible структурой используется точный URL узла (без секретных query-параметров); ответ API должен содержать choices[0].message.content.\n\n"
            f"В этом workflow обнаружены провайдеры: {provider_names}.\n\n"
            "Распознанные HTTP AI-запросы с поддерживаемой chat-моделью и n8n AI Agent используют `create_agent` с моделью выбранного провайдера. Gemini Interactions вызывается отдельно через асинхронный Google GenAI SDK, чтобы сохранить настройки удалённого агента. Секреты задаются в `config.py` или переменных окружения.\n"
        )
    return files


def _langchain_package(provider: str | None) -> str:
    """Вернуть имя пакета провайдера без общего префикса ``langchain-``."""
    packages = {
        "openai": "openai",
        "deepseek": "openai",
        "openrouter": "openai",
        "openai_compatible": "openai",
        "anthropic": "anthropic",
        "gemini": "google-genai",
        "gemini_compatible": "google-genai",
        "ollama": "ollama",
    }
    if provider not in packages:
        raise WorkflowError(f"Неизвестный AI-провайдер LangGraph: {provider}")
    return packages[provider]


def _langchain_provider_for_model(node: dict[str, Any]) -> str:
    """Определить провайдера по типу узла chat-модели n8n."""
    kind = str(node.get("type", "")).casefold()
    for provider in ("openrouter", "deepseek", "anthropic", "gemini", "google", "ollama", "openai"):
        if provider in kind:
            return "gemini" if provider == "google" else provider
    return "openai_compatible"
