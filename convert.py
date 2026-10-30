"""Запуск команды конвертации через корневой файл проекта.

Скрипт сохраняет короткую команду ``python convert.py workflow.json --out ...``
и передаёт все аргументы модулю Typer без собственной логики конвертации.
"""

import sys

from n8n_transcriper.cli import app


if __name__ == "__main__":
    app(["convert", *sys.argv[1:]])
