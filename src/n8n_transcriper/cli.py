"""Командная строка конвертера."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from n8n_transcriper.graph import connections, execution_order
from n8n_transcriper.reader import WorkflowError, read_workflow
from n8n_transcriper.generator import generate_project

app = typer.Typer(help="Инструменты для преобразования workflow n8n в Python.")


def _echo_terminal_safe(message: object, *, err: bool = False) -> None:
    """Вывести текст, экранируя символы, отсутствующие в кодировке терминала."""
    stream = sys.stderr if err else sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe_message = str(message).encode(encoding, errors="backslashreplace").decode(encoding)
    typer.echo(safe_message, err=err)


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
    stage = "чтение JSON и проверка структуры"
    try:
        data = read_workflow(workflow)
        stage = "проверка графа и порядка соединений"
        edges = connections(data)
        execution_order(data)

        stage = "проверка поддержки узлов, настроек и expressions"
        with TemporaryDirectory(prefix="n8n-transcriper-validate-") as temporary_directory:
            output = Path(temporary_directory) / "generated"
            generate_project(data, output)

            stage = "проверка синтаксиса сгенерированного Python"
            python_files = sorted(output.rglob("*.py"))
            for source_path in python_files:
                source = source_path.read_text(encoding="utf-8")
                try:
                    compile(source, str(source_path), "exec")
                except SyntaxError as exc:
                    relative_path = source_path.relative_to(output)
                    raise WorkflowError(
                        f"{relative_path}:{exc.lineno}:{exc.offset}: {exc.msg}"
                    ) from exc

        trigger_count = sum(
            node["type"].rsplit(".", 1)[-1].lower().endswith("trigger")
            or node["type"] == "n8n-nodes-base.webhook"
            for node in data["nodes"]
        )
    except Exception as exc:
        _echo_terminal_safe("Проверка workflow не пройдена.", err=True)
        _echo_terminal_safe(f"Файл: {workflow}", err=True)
        _echo_terminal_safe(f"Этап: {stage}", err=True)
        _echo_terminal_safe(f"Тип ошибки: {type(exc).__name__}", err=True)
        _echo_terminal_safe(f"Подробности: {exc}", err=True)
        raise typer.Exit(1) from exc

    _echo_terminal_safe("Проверка workflow пройдена.")
    _echo_terminal_safe(f"Файл: {workflow}")
    _echo_terminal_safe(f"Workflow: {data.get('name') or 'без имени'}")
    _echo_terminal_safe(
        f"Узлов: {len(data['nodes'])}; соединений: {len(edges)}; "
        f"триггеров будет пропущено: {trigger_count}."
    )
    _echo_terminal_safe(f"Синтаксис сгенерированного Python проверен: {len(python_files)} файлов.")
    _echo_terminal_safe("Ошибок совместимости, графа и expressions не найдено.")
