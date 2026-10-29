"""Чтение JSON-файлов n8n и проверка их структуры."""

import errno
import json
from pathlib import Path
from typing import Any


class WorkflowError(ValueError):
    """Ошибка чтения или проверки workflow."""


def load_workflow(path: str | Path) -> Any:
    """Прочитать JSON workflow, не проверяя его структуру."""
    workflow_path = Path(path)
    try:
        with workflow_path.open(encoding="utf-8-sig") as source:
            return json.load(source)
    except json.JSONDecodeError as exc:
        reason = _json_error_reason(exc.msg)
        raise WorkflowError(
            f"Файл «{workflow_path}»: ошибка JSON ({reason}), "
            f"строка {exc.lineno}, столбец {exc.colno}."
        ) from exc
    except UnicodeDecodeError as exc:
        raise WorkflowError(
            f"Файл «{workflow_path}» не является корректным текстом UTF-8 "
            f"(ошибка около байта {exc.start})."
        ) from exc
    except UnicodeError as exc:
        raise WorkflowError(f"Не удалось декодировать файл «{workflow_path}» как UTF-8.") from exc
    except OSError as exc:
        reason = {
            errno.ENOENT: "файл не найден",
            errno.EACCES: "нет доступа к файлу",
            errno.EISDIR: "указанный путь является папкой",
        }.get(exc.errno, "не удалось открыть файл")
        raise WorkflowError(f"{reason.capitalize()}: «{workflow_path}».") from exc


def read_workflow(path: str | Path) -> dict[str, Any]:
    """Прочитать UTF-8 JSON и проверить структуру workflow."""
    workflow = load_workflow(path)
    validate_workflow(workflow)
    return workflow


def validate_workflow_issues(workflow: Any) -> list[str]:
    """Собрать все обнаруживаемые ошибки структуры workflow."""
    if not isinstance(workflow, dict):
        return ["Корневое значение workflow должно быть JSON-объектом."]

    issues: list[str] = []
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list):
        issues.append("Поле «nodes» должно быть списком.")
    if not isinstance(connections, dict):
        issues.append("Поле «connections» должно быть объектом.")

    names: set[str] = set()
    ids: set[str] = set()
    if isinstance(nodes, list):
        for index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                issues.append(f"Узел №{index} должен быть объектом.")
                continue

            name = node.get("name")
            node_label = f"Узел «{name}»" if isinstance(name, str) and name else f"Узел №{index}"
            node_is_valid = True
            for key, label in (("id", "id"), ("name", "имя"), ("type", "тип")):
                value = node.get(key)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"{node_label}: поле «{label}» должно быть непустой строкой.")
                    node_is_valid = False

            if isinstance(name, str) and name:
                if name in names:
                    issues.append(f"{node_label}: имя узла повторяется.")
                    node_is_valid = False
                else:
                    names.add(name)

            node_id = node.get("id")
            if isinstance(node_id, str) and node_id:
                if node_id in ids:
                    issues.append(f"{node_label}: идентификатор узла повторяется.")
                    node_is_valid = False
                else:
                    ids.add(node_id)

            if not isinstance(node.get("parameters"), dict):
                issues.append(f"{node_label}: поле «parameters» должно быть объектом.")
                node_is_valid = False

    if isinstance(connections, dict):
        for source, channels in connections.items():
            if not isinstance(source, str) or not source:
                issues.append("Имя исходного узла в поле «connections» должно быть непустой строкой.")
            elif isinstance(nodes, list) and source not in names:
                issues.append(f"Не найден исходный узел соединения «{source}».")
            if not isinstance(channels, dict):
                issues.append(f"Соединения узла «{source}» должны быть объектом.")
                continue

            for channel, outputs in channels.items():
                if not isinstance(outputs, list):
                    issues.append(f"Узел «{source}»: выходы канала «{channel}» должны быть списком.")
                    continue

                for output_index, output in enumerate(outputs):
                    if not isinstance(output, list):
                        issues.append(
                            f"Узел «{source}»: список соединений выхода {output_index} "
                            f"канала «{channel}» должен быть списком."
                        )
                        continue

                    for target_index, target in enumerate(output):
                        location = (
                            f"Узел «{source}», канал «{channel}», "
                            f"выход {output_index}, соединение {target_index}"
                        )
                        if not isinstance(target, dict):
                            issues.append(f"{location}: соединение должно быть объектом.")
                            continue

                        target_name = target.get("node")
                        if not isinstance(target_name, str) or not target_name:
                            issues.append(f"{location}: имя целевого узла должно быть непустой строкой.")
                        elif isinstance(nodes, list) and target_name not in names:
                            issues.append(f"{location}: целевой узел «{target_name}» не найден.")
                        target_type = target.get("type")
                        if not isinstance(target_type, str) or not target_type:
                            issues.append(f"{location}: тип соединения должен быть непустой строкой.")
                        target_input = target.get("index")
                        if (
                            not isinstance(target_input, int)
                            or isinstance(target_input, bool)
                            or target_input < 0
                        ):
                            issues.append(f"{location}: индекс входа должен быть целым числом не меньше нуля.")

    return issues


def validate_workflow(workflow: Any) -> None:
    """Проверить структуру и сообщить обо всех найденных ошибках."""
    issues = validate_workflow_issues(workflow)
    if issues:
        details = "\n".join(f"- {issue}" for issue in issues)
        raise WorkflowError(f"Структура workflow содержит ошибки:\n{details}")


def _json_error_reason(message: str) -> str:
    """Перевести распространённые сообщения JSON-декодера на русский."""
    if message.startswith("Expecting property name"):
        return "имя свойства должно быть заключено в двойные кавычки"
    if message.startswith("Expecting ':'"):
        return "после имени свойства пропущено двоеточие"
    if message.startswith("Expecting ','"):
        return "между элементами пропущена запятая"
    if message.startswith("Expecting value"):
        return "ожидалось значение"
    if message.startswith("Unterminated string"):
        return "строка не закрыта кавычкой"
    if message.startswith("Invalid \\escape"):
        return "обнаружена недопустимая escape-последовательность"
    if message == "Extra data":
        return "после JSON найдены лишние данные"
    return "синтаксическая ошибка"
