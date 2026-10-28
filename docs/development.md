# Установка, команды и разработка

## Окружение

Нужны Python 3.11+ и `uv`. В корне репозитория:

```bash
uv sync
```

`uv.lock` фиксирует версии зависимостей. Команда устанавливает пакет в локальное виртуальное окружение и инструменты разработки.

## CLI

```bash
uv run n8n-transcriper --help
uv run n8n-transcriper convert workflow.json --out generated
uv run n8n-transcriper inspect workflow.json
```

Для быстрого запуска преобразования также есть wrapper:

```bash
uv run python convert.py workflow.json --out generated
```

`inspect` читает и проверяет workflow, затем перечисляет узлы. `convert` записывает проект в отсутствующую или пустую папку назначения.

## Сгенерированный проект

В `generated/README.md` описаны конкретные зависимости и credentials результата. Обычно его запуск выглядит так:

```bash
cd generated
uv venv
uv pip install -r requirements.txt
```

Запустите `.venv\\Scripts\\python.exe main.py` в Windows PowerShell или `.venv/bin/python main.py` в Linux/macOS. Переменные секретов задаются в окружении процесса; `.env` без дополнительной библиотеки автоматически не читается.

Для импорта используйте `from main import run` и передавайте `run()` список items формата `[{"json": {...}}]`.

## Тесты

Запуск unit и mock-интеграционных тестов:

```bash
uv run pytest
```

Тесты HTTP, DeepSeek и Gmail используют mock-ответы; отправка реальной почты и запросы к API не требуются.

`tests/test_postgres_integration.py` проверяет работу с PostgreSQL и пропускается без `N8N_TEST_POSTGRES_PORT`. Тест подключается к базе `workflow_test` как `postgres` с тестовым паролем `temporary_test_password`, поэтому используйте только отдельную локальную тестовую БД. Для запуска задайте порт сервера, доступного на localhost:

```powershell
$env:N8N_TEST_POSTGRES_PORT = "5432"
uv run pytest tests/test_postgres_integration.py -q
```

```bash
N8N_TEST_POSTGRES_PORT=5432 uv run pytest tests/test_postgres_integration.py -q
```

## Правила изменений

- Начинайте работу в отдельной `feature/*` ветке от `develop`.
- Для новой возможности добавляйте тесты и проверяйте сгенерированный код, а не только успешное создание файлов.
- Перед коммитом запускайте связанные тесты и `uv run pytest` для интеграционной проверки.
- Не включайте в коммиты `AGENTS.md` и `history.md`; `history.md` ведётся локально.
- Сначала объединяйте проверенную feature-ветку в `develop`; после финальной проверки переносите стабильный результат в `main`.
