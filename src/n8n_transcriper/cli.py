"""Командная строка конвертера."""
import copy
import subprocess
import sys
from pathlib import Path

import typer

from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import (
    WorkflowError,
    load_workflow,
    read_workflow,
    validate_workflow_issues,
)
from n8n_transcriper.generator import generate_project, node_conversion_issues

app = typer.Typer(help="Инструменты для преобразования workflow n8n в Python.")


def _echo_terminal_safe(message: object, *, err: bool = False) -> None:
    """Вывести текст, экранируя символы, отсутствующие в кодировке терминала."""
    stream = sys.stderr if err else sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe_message = str(message).encode(encoding, errors="backslashreplace").decode(encoding)
    typer.echo(safe_message, err=err)


def _create_uv_lock(project: Path) -> None:
    """Разрешить зависимости созданного проекта и записать uv.lock."""
    try:
        result = subprocess.run(
            ["uv", "lock"],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkflowError("Не найден uv. Установите uv и повторите команду convert.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        message = f"Не удалось создать uv.lock командой `uv lock` (код {result.returncode})."
        if detail:
            message += f"\n{detail}"
        raise WorkflowError(message)


def _node_name_for_error(message: str, nodes: list[dict]) -> str | None:
    """Найти имя узла в стандартном сообщении об ошибке конвертера."""
    names = [node.get("name") for node in nodes if isinstance(node, dict)]
    for name in sorted((name for name in names if isinstance(name, str)), key=len, reverse=True):
        if message.startswith(f"Узел {name}:") or message.startswith(f"Узел «{name}»:"):
            return name
    return None


def _make_validation_placeholder(node: dict) -> None:
    """Подменить проблемный узел простым pass-through только для продолжения проверки."""
    node["type"] = "n8n-nodes-base.code"
    node["parameters"] = {"language": "pythonNative", "pythonCode": "return _items"}
    node["disabled"] = False
    node["continueOnFail"] = False
    node["alwaysOutputData"] = False
    node["executeOnce"] = False
    node["onError"] = "stopWorkflow"
    node["retryOnFail"] = False


def _validate_generated_project(
    workflow: dict,
    known_invalid_nodes: set[str],
) -> tuple[list[str], int]:
    """Продолжать проверку генерации, временно пропуская каждый проблемный узел."""
    issues: list[str] = []
    invalid_nodes = set(known_invalid_nodes)
    nodes = workflow["nodes"]

    for _ in range(len(nodes) + 1):
        candidate = copy.deepcopy(workflow)
        for node in candidate["nodes"]:
            if node["name"] in invalid_nodes:
                _make_validation_placeholder(node)

        try:
            files = generate_project(candidate)
        except WorkflowError as exc:
            name = _node_name_for_error(str(exc), nodes)
            if name is not None and name not in invalid_nodes:
                issues.append(str(exc))
                invalid_nodes.add(name)
                continue
            issues.append(f"Ошибка генерации проекта: {exc}")
            return issues, 0

        python_files = sorted(
            (relative_path, source)
            for relative_path, source in files.items()
            if relative_path.endswith(".py")
        )
        for relative_path, source in python_files:
            try:
                compile(source, relative_path, "exec")
            except SyntaxError as exc:
                reason = {
                    "invalid syntax": "неверный синтаксис",
                    "expected ':'": "ожидалось двоеточие",
                    "unexpected indent": "неожиданный отступ",
                    "unindent does not match any outer indentation level": "отступ не соответствует окружающему коду",
                }.get(exc.msg, "синтаксическая ошибка")
                issues.append(
                    f"Файл {relative_path}, строка {exc.lineno}, столбец {exc.offset}: {reason}."
                )
        return issues, len(python_files)

    issues.append("Не удалось завершить проверку всех узлов.")
    return issues, 0


@app.callback()
def main() -> None:
    """Работа с workflow n8n."""


@app.command()
def convert(
    workflow: Path = typer.Argument(help="Путь к JSON-файлу workflow."),
    out: Path = typer.Option(..., "--out", help="Пустая папка для сгенерированного проекта."),
) -> None:
    """Преобразовать поддерживаемый workflow в проект Python."""
    try:
        generate_project(read_workflow(workflow), out)
        _create_uv_lock(out)
    except (WorkflowError, OSError) as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Проект создан: {out}")


@app.command()
def inspect(workflow: Path = typer.Argument(help="Путь к JSON-файлу workflow.")) -> None:
    """Проверить файл и показать список узлов workflow."""
    try:
        data = read_workflow(workflow)
    except WorkflowError as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Workflow: {data.get('name', 'без имени')}")
    typer.echo(f"Узлов: {len(data['nodes'])}")
    for node in data["nodes"]:
        typer.echo(f"  {node['name']} ({node['type']})")


@app.command()
def validate(workflow: Path = typer.Argument(help="Путь к JSON-файлу workflow.")) -> None:
    """Подробно проверить workflow и совместимость с конвертером."""
    issues: list[tuple[str, str]] = []
    try:
        data = load_workflow(workflow)
    except WorkflowError as exc:
        _echo_terminal_safe("Проверка workflow не пройдена.", err=True)
        _echo_terminal_safe(f"Файл: {workflow}", err=True)
        _echo_terminal_safe(f"Подробности: {exc}", err=True)
        raise typer.Exit(1) from exc

    structural_issues = validate_workflow_issues(data)
    issues.extend(("проверка структуры", issue) for issue in structural_issues)
    node_issues = node_conversion_issues(data)
    issues.extend(("проверка узлов и настроек", issue) for _, issue in node_issues)

    edges = []
    graph_is_valid = False
    if not structural_issues:
        try:
            edges = connections(data)
            execution_order(data)
            graph_is_valid = True
        except WorkflowError as exc:
            issues.append(("проверка графа", str(exc)))

    python_file_count = 0
    if not structural_issues and graph_is_valid:
        try:
            generation_issues, python_file_count = _validate_generated_project(
                data,
                {name for name, _ in node_issues if name is not None},
            )
            issues.extend(("проверка узлов, настроек и expressions", issue) for issue in generation_issues)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            issues.append(("проверка генерации", f"Не удалось завершить проверку генерации: {exc}"))

    if issues:
        _echo_terminal_safe("Проверка workflow не пройдена.", err=True)
        _echo_terminal_safe(f"Файл: {workflow}", err=True)
        _echo_terminal_safe(f"Обнаружено проблем: {len(issues)}", err=True)
        for index, (stage, issue) in enumerate(issues, start=1):
            _echo_terminal_safe(f"{index}. [{stage}] {issue}", err=True)
        raise typer.Exit(1)

    trigger_count = sum(
        node["type"].rsplit(".", 1)[-1].lower().endswith("trigger")
        or node["type"] == "n8n-nodes-base.webhook"
        for node in data["nodes"]
    )
    _echo_terminal_safe("Проверка workflow пройдена.")
    _echo_terminal_safe(f"Файл: {workflow}")
    _echo_terminal_safe(f"Workflow: {data.get('name') or 'без имени'}")
    _echo_terminal_safe(
        f"Узлов: {len(data['nodes'])}; соединений: {len(edges)}; "
        f"триггеров будет пропущено: {trigger_count}."
    )
    _echo_terminal_safe(f"Синтаксис сгенерированного Python проверен: {python_file_count} файлов.")
    _echo_terminal_safe("Ошибок совместимости, графа и expressions не найдено.")
