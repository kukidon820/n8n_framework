# n8n-transcriper

Конвертер экспортированных n8n workflow в самостоятельные Python-проекты. Он разбирает граф и expressions при генерации, а в `generated/main.py` записывает обычные вызовы функций и ветвления. Для запуска результата n8n и исходный JSON не нужны.

Конвертер ориентирован на понятный сгенерированный код и поддерживаемые типы узлов. Если встречается неизвестная операция, настройка или неоднозначное выражение, генерация завершается диагностикой вместо тихой подмены поведения.

## Быстрый запуск

Требуются Python 3.11+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python convert.py workflow.json --out generated
```

Каталог `generated` должен отсутствовать или быть пустым. В нём появятся `main.py`, `config.py`, `requirements.txt`, `README.md`, `conversion_report.md` и модули для используемых узлов.

## Команды

Посмотреть команды:

```bash
uv run n8n-transcriper --help
```

Преобразовать workflow:

```bash
uv run n8n-transcriper convert workflow.json --out generated
```

Проверить файл и вывести узлы:

```bash
uv run n8n-transcriper inspect workflow.json
```

Короткая команда `uv run python convert.py workflow.json --out generated` эквивалентна команде `convert`.

## Запуск сгенерированного проекта

Перейдите в каталог результата, создайте окружение и установите только нужные этому workflow зависимости:

```bash
cd generated
uv venv
uv pip install -r requirements.txt
```

Windows PowerShell:

```powershell
.venv\Scripts\python.exe main.py
```

Linux/macOS:

```bash
.venv/bin/python main.py
```

Триггеры n8n не запускаются. Если workflow ожидает входные данные, передайте items вручную:

```python
from main import run

result = run([{"json": {"name": "Пример"}}])
```

Без аргумента `run()` получает один пустой item `{"json": {}}`. Исходный workflow JSON после генерации не требуется.

## Credentials

`config.py` перечисляет переменные окружения, которые использует сгенерированный проект. Перед запуском задайте значения в терминале или в окружении процесса. Файл `.env` автоматически не загружается.

```powershell
$env:DEEPSEEK_API_KEY = "..."
$env:GMAIL_CLIENT_ID = "..."
$env:GMAIL_CLIENT_SECRET = "..."
$env:GMAIL_REFRESH_TOKEN = "..."
```

```bash
export DEEPSEEK_API_KEY="..."
export GMAIL_CLIENT_ID="..."
export GMAIL_CLIENT_SECRET="..."
export GMAIL_REFRESH_TOKEN="..."
```

Заполняйте только переменные, перечисленные в `generated/config.py`. Не добавляйте реальные ключи и пароли в Git.

## Поддерживаемые узлы

| Узел | Реализация |
|---|---|
| Python Code | Исходный Python-код переносится в функцию; поддерживаются режимы «все items» и «каждый item» |
| HTTP Request | Отдельная функция для каждого узла, базовые методы, тела запросов, заголовки, параметры, credentials, таймауты, повторы и форматы ответов |
| Convert to File, Compression, Extract From File | Текстовые файлы, ZIP, распаковка, извлечение текста и base64 |
| Edit Fields (Set) | Присваивания, типы значений, выбор сохраняемых полей и ссылки на предыдущие узлы |
| IF и Switch | Условия, правила маршрутизации, fallback и несколько совпавших выходов |
| Merge | Только режимы Append и Combine; варианты Combine включают сопоставление по полям, позициям и все пары |
| AI Agent + DeepSeek Chat Model | Обычный HTTP-вызов DeepSeek для агента без подключённых инструментов |
| PostgreSQL | `executeQuery` с параметрами; SELECT/INSERT/UPDATE/DELETE и транзакционная пакетная обработка |

Триггеры пропускаются. Описание реализованных параметров и ограничений приведено в [таблице поддержки](docs/supported-nodes.md).

## Expressions и передача данных

Конвертер преобразует поддерживаемые ссылки на текущие и именованные items, включая `$json`, `$binary`, `$input`, `$('Имя узла').first()`, `.all()` и связанные `.item`. Простые логические выражения, сравнения, шаблоны и `JSON.stringify` компилируются в Python при генерации.

Функции обмениваются n8n-подобными items: `{"json": {...}, "binary": {...}}`. Бинарные данные остаются байтами. Для ссылок `.item` генератор сохраняет связь элементов через IF, Switch и Merge; неоднозначное соответствие вызывает ошибку при выполнении, а не выбирает случайный item.

## Проверки и разработка

Запустить тесты без реальных API:

```bash
uv run pytest
```

Тест PostgreSQL integration требует доступный тестовый сервер и `N8N_TEST_POSTGRES_PORT`. Подробные команды и структура проекта описаны в [руководстве разработчика](docs/development.md) и [обзоре архитектуры](docs/architecture.md).
