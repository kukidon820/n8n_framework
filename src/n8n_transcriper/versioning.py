"""Совместимость версий узлов n8n и заметки для ручной доработки.

Конвертер использует ближайший известный формат узла, если его ``typeVersion``
вышел за проверенный диапазон. Исходная версия при этом сохраняется в
диагностике, README и аргументе generated-функции ``n8n_manual_parameters``.
"""

from __future__ import annotations

import math
import re
from typing import Any


_FILE_NODE_TYPES = {
    "n8n-nodes-base.convertToFile",
    "n8n-nodes-base.compression",
    "n8n-nodes-base.extractFromFile",
}


def node_version_info(node: dict[str, Any]) -> dict[str, Any] | None:
    """Вернуть fallback и пояснение, если версия узла не проверена.

    Диапазоны здесь намеренно ограничены версиями, для которых в конвертерах
    уже есть конкретные предположения о формате. Для остальных типов нельзя
    достоверно объявить версию неподдерживаемой, пока конвертер не задаёт
    проверенный диапазон.
    """
    kind = node.get("type")
    raw_version = node.get("typeVersion")
    numeric = (
        isinstance(raw_version, (int, float))
        and not isinstance(raw_version, bool)
        and (not isinstance(raw_version, float) or math.isfinite(raw_version))
    )

    fallback: int | float | None = None
    supported = ""
    if kind == "n8n-nodes-base.merge":
        supported = "3.x"
        if not numeric or not 3 <= raw_version < 4:
            fallback = 3
    elif kind == "n8n-nodes-base.postgres":
        supported = "2.8 и новее"
        if not numeric or raw_version < 2.8:
            fallback = 2.8
    elif kind == "n8n-nodes-base.set":
        supported = "3.3–3.5"
        if not numeric:
            fallback = 3.5
        elif raw_version < 3.3:
            fallback = 3.3
        elif raw_version > 3.5:
            fallback = 3.5
    elif kind == "n8n-nodes-base.splitInBatches":
        supported = "основные версии 1, 2 и 3"
        if "typeVersion" not in node:
            return None  # Сам конвертер использует версию 3 по умолчанию.
        if not numeric:
            fallback = 3
        elif int(raw_version) < 1:
            fallback = 1
        elif int(raw_version) > 3:
            fallback = 3
    elif kind == "n8n-nodes-base.httpRequest":
        if raw_version is not None and not numeric:
            supported = "числовая версия (fallback: 4)"
            fallback = 4
    elif kind == "n8n-nodes-base.emailSend":
        if raw_version is not None and not numeric:
            supported = "числовая версия (fallback: 2.1)"
            fallback = 2.1
    elif kind in _FILE_NODE_TYPES:
        if raw_version is not None and not numeric:
            supported = "числовая версия (fallback: 1)"
            fallback = 1

    if fallback is None:
        return None

    return {
        "actual": raw_version,
        "fallback": fallback,
        "supported": supported,
        "kind": kind,
    }


def compatible_node(node: dict[str, Any]) -> dict[str, Any]:
    """Скопировать узел, подставив ближайшую известную версию конвертера."""
    compatible = dict(node)
    info = node_version_info(node)
    if info is not None:
        compatible["typeVersion"] = info["fallback"]
        if node.get("type") == "n8n-nodes-base.splitInBatches":
            original_parameters = node.get("parameters")
            parameters = dict(original_parameters) if isinstance(original_parameters, dict) else {}
            parameters = {
                key: value
                for key, value in parameters.items()
                if key in {"batchSize", "options"}
            }
            batch_size = parameters.get("batchSize")
            if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
                parameters.pop("batchSize", None)
            options = parameters.get("options", {})
            if not isinstance(options, dict):
                options = {}
            options = {key: value for key, value in options.items() if key == "reset"}
            if not isinstance(options.get("reset", False), bool):
                options.pop("reset", None)
            parameters["options"] = options
            compatible["parameters"] = parameters
    return compatible


def node_version_warning(node: dict[str, Any]) -> str | None:
    """Сформировать русское предупреждение без остановки конвертации."""
    info = node_version_info(node)
    if info is None:
        return None
    name = node.get("name") or "без имени"
    return (
        f"Узел {name}: версия n8n {info['actual']!r} не входит в проверенный диапазон "
        f"{info['supported']}. Конвертация продолжится с fallback-версией "
        f"{info['fallback']}; параметры и поведение этой версии требуют ручной проверки."
    )


def node_version_warnings(workflow: Any) -> list[tuple[str | None, str]]:
    """Собрать предупреждения о версиях всех обычных узлов workflow."""
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
        return []

    warnings: list[tuple[str | None, str]] = []
    for node in workflow["nodes"]:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if not isinstance(kind, str):
            continue
        # Триггеры пропускаются при генерации, поэтому их версия не влияет на код.
        if kind.rsplit(".", 1)[-1].lower().endswith("trigger") or kind == "n8n-nodes-base.webhook":
            continue
        message = node_version_warning(node)
        if message:
            name = node.get("name") if isinstance(node.get("name"), str) else None
            warnings.append((name, message))
    return warnings


def _parameter_paths(value: Any, prefix: str = "") -> list[str]:
    """Перечислить пути параметров, не копируя в generated-проект их значения.

    Значения не встраиваются в исходники: даже произвольное поле ``value`` может
    содержать токен, пароль или заголовок Authorization.
    """
    if isinstance(value, dict):
        if not value:
            return [prefix] if prefix else []
        paths: list[str] = []
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{prefix}.{key_text}" if prefix else key_text
            paths.extend(_parameter_paths(child, child_path))
        return paths
    if isinstance(value, list):
        if not value:
            return [prefix] if prefix else []
        paths = []
        for index, child in enumerate(value):
            child_path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_parameter_paths(child, child_path))
        return paths
    return [prefix] if prefix else []


def _parameter_template(value: Any) -> Any:
    """Сохранить форму параметров, заменив каждый leaf на ``None``."""
    if isinstance(value, dict):
        return {str(key): _parameter_template(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_parameter_template(child) for child in value]
    return None


def _removed_parameter_paths(original: Any, supported: Any, prefix: str = "parameters") -> list[str]:
    """Найти исходные пути, исключённые из fallback-представления узла."""
    if isinstance(original, dict) and isinstance(supported, dict):
        paths = [f"{prefix}.{key}" for key in original.keys() - supported.keys()]
        for key in original.keys() & supported.keys():
            paths.extend(
                _removed_parameter_paths(original[key], supported[key], f"{prefix}.{key}")
            )
        return paths
    if isinstance(original, list) and isinstance(supported, list):
        paths = []
        for index, value in enumerate(original[: len(supported)]):
            paths.extend(
                _removed_parameter_paths(value, supported[index], f"{prefix}[{index}]")
            )
        paths.extend(f"{prefix}[{index}]" for index in range(len(supported), len(original)))
        return paths
    return []


def manual_parameter_payload(node: dict[str, Any]) -> dict[str, Any]:
    """Подготовить безопасную подсказку, передаваемую generated-узлу.

    В payload сохраняются исходная и fallback-версии, форма настроек с
    ``None`` вместо значений и имена путей. Секреты не копируются в generated-код.
    """
    info = node_version_info(node)
    if info is None:
        return {}
    original_version = info["actual"]
    if isinstance(original_version, float) and not math.isfinite(original_version):
        original_version = repr(original_version)
    compatible_parameters = compatible_node(node).get("parameters", {})
    unsupported_paths = _removed_parameter_paths(
        node.get("parameters", {}), compatible_parameters
    )
    return {
        "typeVersion": original_version,
        "fallback_version": info["fallback"],
        "parameters_template": _parameter_template(node.get("parameters", {})),
        "parameter_paths": tuple(_parameter_paths(node.get("parameters", {}))),
        "unsupported_parameter_paths": tuple(unsupported_paths),
    }


def add_manual_parameter_to_source(
    source: str,
    function_name: str,
    warning: str,
) -> str:
    """Добавить в generated-функцию аргумент и TODO-комментарии о fallback."""
    pattern = re.compile(rf"(?m)^(def {re.escape(function_name)}\()([^\n]*)(\):)$")
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"Не удалось найти функцию {function_name} для пометки версии")
    arguments = match.group(2)
    if "n8n_manual_parameters" in arguments:
        return source
    arguments = arguments.rstrip()
    if "*" in arguments:
        separator = ", " if arguments else ""
    else:
        separator = ", *, " if arguments else "*, "
    replacement = (
        match.group(1)
        + arguments
        + separator
        + "n8n_manual_parameters=None"
        + match.group(3)
    )
    start, end = match.span()
    source = source[:start] + replacement + source[end:]
    line_end = source.find("\n", start)
    if line_end < 0:
        line_end = len(source)
    safe_warning = warning.replace("\r", " ").replace("\n", " ")
    comment = (
        "\n    # TODO: проверить и вручную реализовать поведение исходной версии n8n."
        f"\n    # {safe_warning}"
        "\n    # Структура настроек передана в n8n_manual_parameters['parameters_template'];"
        "\n    # заполните значения по исходному workflow и реализуйте неизвестные поля."
        "\n    # Секреты не копируются из workflow в generated-код."
    )
    return source[:line_end] + comment + source[line_end:]
