"""Typer CLI для просмотра, проверки и конвертации workflow n8n.

Команды работают с JSON на диске, показывают диагностики на русском и создают
независимый Python-проект только после успешной проверки совместимости.
"""
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
from n8n_transcriper.versioning import node_version_warnings

app = typer.Typer(help="Инструменты для преобразования workflow n8n в Python.")


def _echo_terminal_safe(message: object, *, err: bool = False) -> None:
    """Безопасно вывести диагностическое сообщение в текущую кодировку терминала.

    Args:
        message: Любое значение, которое нужно показать пользователю.
        err: Если True, писать в stderr; иначе — в stdout.

    Символы, которые терминал не умеет отображать, выводятся как escape-последовательности.
    """
    stream = sys.stderr if err else sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe_message = str(message).encode(encoding, errors="backslashreplace").decode(encoding)
    typer.echo(safe_message, err=err)


def _create_uv_lock(project: Path) -> None:
    """Запустить ``uv lock`` в generated-проекте и проверить результат.

    Args:
        project: Корневая папка проекта с ``pyproject.toml``.

    Raises:
        WorkflowError: если uv отсутствует или разрешение зависимостей завершилось ошибкой.
    """
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
    """Определить узел, названный в начале диагностики генератора.

    Имена проверяются от длинного к короткому, чтобы имя узла-префикса не
    перехватило сообщение о другом узле с более длинным именем.
    """
    names = [node.get("name") for node in nodes if isinstance(node, dict)]
    for name in sorted((name for name in names if isinstance(name, str)), key=len, reverse=True):
        if message.startswith(f"Узел {name}:") or message.startswith(f"Узел «{name}»:"):
            return name
    return None


def _make_validation_placeholder(node: dict) -> None:
    """Временно сделать копию узла pass-through для дальнейшего анализа графа.

    Заглушка применяется только к глубокой копии workflow внутри ``validate``.
    Исходный JSON и создаваемый пользователем проект не меняются.
    """
    node["type"] = "n8n-nodes-base.code"
    node["parameters"] = {"language": "pythonNative", "pythonCode": "return _items"}
    node["disabled"] = False
    node["continueOnFail"] = False
    node["alwaysOutputData"] = False
    node["executeOnce"] = False
    node["onError"] = "stopWorkflow"
    node["retryOnFail"] = False


def _indentation_error_details(source: str, error: IndentationError) -> str:
    """Объяснить ошибку отступа по уровням соседних строк без вывода исходного кода."""
    lines = source.splitlines()

    def indentation(line: str) -> str:
        prefix = line[: len(line) - len(line.lstrip(" \t"))]
        spaces = prefix.count(" ")
        tabs = prefix.count("\t")
        parts = []
        if spaces:
            parts.append(f"{spaces} пробелов")
        if tabs:
            parts.append(f"{tabs} табуляций")
        return " и ".join(parts) if parts else "без отступа"

    error_line = max(1, error.lineno or 1)
    # Ищем частый случай генерации: docstring функции вложен сильнее ее команд.
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith(('"""', "'''")):
            continue
        previous = next((lines[pos].strip() for pos in range(index - 1, -1, -1) if lines[pos].strip()), "")
        following_index = next(
            (pos for pos in range(index + 1, len(lines)) if lines[pos].strip()), None
        )
        if not previous.startswith("def ") or following_index is None:
            continue
        following = lines[following_index]
        doc_indent = indentation(line)
        code_indent = indentation(following)
        if doc_indent != code_indent and abs((index + 1) - error_line) <= 2:
            return (
                f"строка {index + 1} содержит docstring функции с отступом {doc_indent}, "
                f"а следующая команда в строке {following_index + 1} начинается с {code_indent}. "
                "Python не может определить единый уровень тела функции. Поставьте docstring "
                "на один уровень с командами функции (обычно 4 пробела)."
            )

    reason = {
        "unexpected indent": "перед командой появился лишний отступ",
        "unindent does not match any outer indentation level": "отступ команды не совпадает с уровнем внешнего блока",
        "inconsistent use of tabs and spaces in indentation": "в отступах смешаны табуляции и пробелы",
    }.get(error.msg, "Python не смог разобрать уровни отступов")
    nearby = []
    for index in range(max(0, error_line - 2), min(len(lines), error_line + 1)):
        if lines[index].strip():
            nearby.append(f"строка {index + 1}: {indentation(lines[index])} перед кодом")
    details = f"{reason}. Проверьте, чтобы строки одного блока имели согласованные отступы."
    if nearby:
        details += " Уровни рядом: " + "; ".join(nearby) + "."
    return details


def _validate_generated_project(
    workflow: dict,
    known_invalid_nodes: set[str],
) -> tuple[list[str], int]:
    """Проверить генерацию файлов, собирая ошибки нескольких узлов.

    После ошибки узла функция временно заменяет его на pass-through в копии
    workflow и повторяет генерацию. На выходе возвращаются найденные сообщения
    и число Python-файлов, которые удалось синтаксически проверить.
    """
    issues: list[str] = []
    invalid_nodes = set(known_invalid_nodes)
    nodes = workflow["nodes"]

    # Каждая итерация исключает не более одного нового проблемного узла.
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

        # Проверяем код в памяти: validate не создаёт временный generated-каталог.
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
                location = f"Файл {relative_path}, строка {exc.lineno}, столбец {exc.offset}: {reason}."
                if isinstance(exc, IndentationError):
                    location += " Причина: " + _indentation_error_details(source, exc)
                issues.append(location)
        return issues, len(python_files)

    issues.append("Не удалось завершить проверку всех узлов.")
    return issues, 0


@app.callback()
def main() -> None:
    """Точка входа Typer; реальные действия выполняют зарегистрированные команды."""


@app.command()
def convert(
    workflow: Path = typer.Argument(help="Путь к JSON-файлу workflow."),
    out: Path = typer.Option(..., "--out", help="Пустая папка для сгенерированного проекта."),
) -> None:
    """Проверить workflow, сгенерировать проект и зафиксировать его зависимости.

    Аргументы:
        workflow: Исходный JSON-файл n8n.
        out: Пустая папка, куда записываются файлы проекта.
    """
    try:
        data = read_workflow(workflow)
        warnings = node_version_warnings(data)
        for _, warning in warnings:
            _echo_terminal_safe(f"Предупреждение: {warning}")
        generate_project(data, out)
        _create_uv_lock(out)
    except (WorkflowError, OSError) as exc:
        typer.echo(f"Ошибка: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Проект создан: {out}")


@app.command()
def inspect(workflow: Path = typer.Argument(help="Путь к JSON-файлу workflow.")) -> None:
    """Проверить JSON и вывести имя workflow и тип каждого узла.

    Команда не генерирует проект и не интерпретирует параметры узлов.
    """
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
    """Собрать структурные, графовые и конверсионные ошибки workflow.

    Проверка выполняется по этапам: чтение JSON, структура, параметры узлов,
    связи и синтаксис сгенерированного Python. Ошибки одного этапа не скрывают
    остальные проверки, которые можно безопасно продолжить.
    """
    issues: list[tuple[str, str]] = []
    try:
        data = load_workflow(workflow)
    except WorkflowError as exc:
        _echo_terminal_safe("Проверка workflow не пройдена.", err=True)
        _echo_terminal_safe(f"Файл: {workflow}", err=True)
        _echo_terminal_safe(f"Подробности: {exc}", err=True)
        raise typer.Exit(1) from exc
    version_warnings = node_version_warnings(data)

    # Сначала собираются независимые ошибки структуры и настроек каждого узла.
    structural_issues = validate_workflow_issues(data)
    issues.extend(("проверка структуры", issue) for issue in structural_issues)
    node_issues = node_conversion_issues(data)
    issues.extend(("проверка узлов и настроек", issue) for _, issue in node_issues)

    edges = []
    graph_is_valid = False
    # Граф нельзя строить, пока имена узлов или форма соединений не проверены.
    if not structural_issues:
        try:
            edges = connections(data)
            execution_order(data)
            graph_is_valid = True
        except WorkflowError as exc:
            issues.append(("проверка графа", str(exc)))

    python_file_count = 0
    # Генерацию запускаем только для структурно корректного и ацикличного графа.
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
        for _, warning in version_warnings:
            _echo_terminal_safe(f"Предупреждение: {warning}", err=True)
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
    for _, warning in version_warnings:
        _echo_terminal_safe(f"Предупреждение: {warning}")
