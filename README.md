# n8n-transcriper

Конвертер экспортированных workflow из n8n в самостоятельные Python-проекты. Он читает JSON только при генерации, анализирует связи и expressions, затем создаёт обычные функции и явный порядок вызовов в <code>main.py</code>. Исходный workflow и n8n для запуска результата не нужны.

Для обычных шагов создаётся отдельный Python-модуль. Ветвления, циклы и объединения преобразуются в видимый Python-код. Конвертер не запускает триггеры и не создаёт runtime-интерпретатор n8n.

## Установка

Нужны Python 3.12 или новее и <code>uv</code>. В корне репозитория выполните:

    uv sync

## Команды

Показать справку:

    uv run n8n-transcriper --help

Проверить структуру workflow и вывести его узлы:

    uv run n8n-transcriper inspect workflow.json

Проверить workflow, поддержку узлов и выражений, а также сгенерированный Python-код без записи проекта:

    uv run n8n-transcriper validate workflow.json

Создать проект:

    uv run n8n-transcriper convert workflow.json --out generated

В качестве короткой команды преобразования также доступно:

    uv run python convert.py workflow.json --out generated

Каталог назначения должен отсутствовать или быть пустым. Конвертер создаст в нём только файлы и зависимости, нужные этому workflow: <code>main.py</code>, <code>config.py</code>, <code>pyproject.toml</code>, <code>uv.lock</code>, <code>requirements.txt</code>, README, отчёт и модули узлов.

## Запуск созданного проекта

Перейдите в каталог результата, установите зафиксированные зависимости и запустите workflow:

    cd generated
    uv sync --locked
    uv run python main.py

Для запуска через скрипт используйте <code>run.bat</code> на Windows или <code>sh run.sh</code> на Linux/macOS. Если workflow ожидает входные данные, передайте их в <code>run()</code> вручную:

    from main import run

    result = run([{"json": {"name": "Пример"}}])

Если аргумент не передан, точка входа получает один пустой item. Элементы имеют n8n-подобную форму: <code>{"json": {...}, "binary": {...}}</code>. Бинарные файлы представлены байтами.

В async-приложении используйте <code>await run_async(input_data)</code> из <code>main.py</code>. Каждый сгенерированный узел — async-функция; HTTP, SMTP и PostgreSQL используют асинхронные клиенты.

## Частичная конвертация

Когда узел, операция, параметр, expression или соединение не поддерживается, конвертер записывает причину в комментарии <code>main.py</code>, в generated README и в <code>conversion_report.md</code>, пропускает непереводимую часть и продолжает генерацию остальной программы. Соединения через пропущенные узлы упрощаются. Из-за этого оставшиеся узлы могут получить не те данные, которые получали в n8n: перед запуском проверьте все пометки и вручную восстановите нужную логику.

Команда <code>validate</code> помогает увидеть проблемы до запуска конвертации. Неправильный JSON и ошибки, которые нельзя локализовать до отдельной части workflow, могут помешать проверке. Полный список поддерживаемых операций и настроек приведён в [справочнике узлов](docs/supported-nodes.md).

## Credentials и переменные окружения

Сгенерированный <code>config.py</code> показывает, какие секреты и настройки необходимы этому проекту. Значения из credentials n8n не переносятся в Python-код. Установите переменные, которые перечислены в сгенерированном конфиге.

PowerShell:

    $env:OPENROUTER_CHAT_MODEL_API_KEY = "ваш-ключ"
    $env:SMTP_USERNAME = "your-account@gmail.com"
    $env:SMTP_APP_PASSWORD = "пароль-приложения"

Linux/macOS:

    export OPENROUTER_CHAT_MODEL_API_KEY="ваш-ключ"
    export SMTP_USERNAME="your-account@gmail.com"
    export SMTP_APP_PASSWORD="пароль-приложения"

Файл <code>.env</code> сам по себе не загружается. Точные имена переменных смотрите в generated <code>config.py</code>; примеры выше могут отличаться от вашего workflow. Не записывайте реальные ключи и пароли в репозиторий.

## Поддерживаемые узлы

| Узел | Реализация |
|---|---|
| Python Code | Перенос исходного Python-кода в функцию; запуск для всех items или каждого item |
| HTTP Request | Отдельный HTTP-вызов с настройками запроса, ответа, авторизации, retry, batching и ограниченной пагинацией |
| Send Email | Создание MIME-письма и отправка через SMTP |
| Convert to File | CSV, HTML, iCal, JSON, ODS, RTF, текст, XLS, XLSX и binary |
| Compression | ZIP-архивирование и распаковка с bytes |
| Extract From File | Чтение текста из binary или преобразование binary в base64 |
| Edit Fields (Set) | Assignments, выбор исходных полей, binary и обработка ошибок приведения типов |
| No Operation | Передача входных items без изменений |
| IF и Switch | Маршрутизация items по условиям |
| Merge | Только Append и Combine |
| Loop Over Items | Класс <code>Loop</code> с размером пакета, reset, Execute Once и Retry On Fail |
| Wait | Асинхронная пауза по интервалу или до заданной даты; webhook/form пропускаются с диагностикой |
| AI Agent | HTTP-вызов подключённой модели через OpenAI-compatible Chat Completions API; tools и streaming не поддерживаются |
| Data Table | Операции со строками PostgreSQL через asyncpg |
| PostgreSQL | Простые SQL-запросы через psycopg |

Справочник [описывает поддерживаемые параметры и ограничения каждого узла](docs/supported-nodes.md). В [обзоре архитектуры](docs/architecture.md) объяснено устройство generated-проекта. Команды установки и разработки приведены в [руководстве разработчика](docs/development.md).

## Проверки и разработка

Запуск unit-тестов:

    uv run pytest

Интеграционная проверка PostgreSQL пропускается, если не задан порт тестового сервера. Условия запуска описаны в [руководстве разработчика](docs/development.md).
