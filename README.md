# n8n-transcriper

Конвертер экспортированных n8n workflow в самостоятельные Python-проекты. Он разбирает граф и expressions при генерации, а в `generated/main.py` записывает обычные вызовы функций и ветвления. Для запуска результата n8n и исходный JSON не нужны.

Конвертер ориентирован на понятный сгенерированный код и поддерживаемые типы узлов. Если встречается неизвестная операция, настройка или неоднозначное выражение, генерация завершается диагностикой вместо тихой подмены поведения.

## Быстрый запуск

Требуются Python 3.12+ и [uv](https://docs.astral.sh/uv/). Файл `.python-version` выбирает Python 3.12 для команд uv.

```bash
uv sync
uv run python convert.py workflow.json --out generated
```

Каталог `generated` должен отсутствовать или быть пустым. В нём появятся `main.py`, `config.py`, `pyproject.toml`, `.python-version`, `uv.lock`, `requirements.txt`, `run.bat`, `run.sh`, `README.md`, `conversion_report.md` и модули для используемых узлов. При конвертации uv разрешит зависимости и создаст lock-файл.

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

Подробно проверить структуру, соединения, поддержку узлов, expressions и синтаксис сгенерированного Python без сохранения файлов:

```bash
uv run n8n-transcriper validate workflow.json
```

Короткая команда `uv run python convert.py workflow.json --out generated` эквивалентна команде `convert`.

## Запуск сгенерированного проекта

Перейдите в каталог результата и установите точные версии из lock-файла:

```bash
cd generated
uv sync --locked
uv run python main.py
```

Для запуска через обёртку используйте `run.bat` на Windows или `sh run.sh` на Linux/macOS. В интерактивном терминале оба скрипта ждут подтверждения после выполнения, чтобы оставить логи на экране. Логирование workflow работает через стандартный вывод на обеих платформах.

Триггеры n8n не запускаются. Если workflow ожидает входные данные, передайте items вручную:

```python
from main import run

result = run([{"json": {"name": "Пример"}}])
```

Без аргумента `run()` получает один пустой item `{"json": {}}`. Исходный workflow JSON после генерации не требуется.

## Credentials

`config.py` перечисляет переменные окружения, которые использует сгенерированный проект. Перед запуском задайте значения в терминале или в окружении процесса. Файл `.env` автоматически не загружается.

```powershell
$env:OPENROUTER_CHAT_MODEL_API_KEY = "..."
$env:SMTP_USERNAME = "your-account@gmail.com"
$env:SMTP_APP_PASSWORD = "пароль-приложения"
```

```bash
export OPENROUTER_CHAT_MODEL_API_KEY="..."
export SMTP_USERNAME="your-account@gmail.com"
export SMTP_APP_PASSWORD="app-password"
```

Заполняйте только переменные, перечисленные в `generated/config.py`. Не добавляйте реальные ключи и пароли в Git.

## Поддерживаемые узлы

| Узел | Реализация |
|---|---|
| Python Code | Исходный Python-код переносится в функцию; поддерживаются режимы «все items» и «каждый item» |
| HTTP Request | Отдельная функция для каждого узла, базовые методы, тела запросов, заголовки, параметры, credentials, таймауты, повторы и форматы ответов |
| Отправка в Gmail API `users/me/messages/send` | SMTP через `smtp.gmail.com:587` с STARTTLS и паролем приложения; остальные Gmail API запросы используют OAuth2 |
| Convert to File, Compression, Extract From File | Текстовые файлы, ZIP, распаковка, извлечение текста и base64 |
| Edit Fields (Set) | Присваивания, типы значений, выбор сохраняемых полей и ссылки на предыдущие узлы |
| IF и Switch | Условия, правила маршрутизации, fallback и несколько совпавших выходов |
| Merge | Только режимы Append и Combine; варианты Combine включают сопоставление по полям, позициям и все пары |
| AI Agent + chat-модель | Обычный HTTP-вызов к OpenAI-compatible Chat Completions API для агента без подключённых инструментов; URL, ключ и параметры модели находятся в `config.py` |
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
