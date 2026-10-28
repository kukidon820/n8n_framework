"""Командная строка конвертера."""

from pathlib import Path

import typer

from n8n_transcriper.reader import WorkflowError, read_workflow
from n8n_transcriper.generator import generate_project

app = typer.Typer(help="Инструменты для преобразования workflow n8n в Python.")


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
