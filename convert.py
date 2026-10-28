"""Запуск Typer CLI в форме, указанной в техническом задании."""

import sys

from n8n_transcriper.cli import app


if __name__ == "__main__":
    app(["convert", *sys.argv[1:]])
