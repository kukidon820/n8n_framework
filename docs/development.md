# Установка, команды и разработка

## Окружение

Нужны Python 3.12 или новее и <code>uv</code>. В корне репозитория установите зависимости:

    uv sync

<code>uv.lock</code> фиксирует версии зависимостей. Команды ниже запускают установленный пакет внутри управляемого uv окружения.

## CLI

    uv run n8n-transcriper --help
    uv run n8n-transcriper inspect workflow.json
    uv run n8n-transcriper validate workflow.json
    uv run n8n-transcriper convert workflow.json --out generated

<code>inspect</code> читает workflow и выводит его узлы. <code>validate</code> проверяет JSON, граф, поддержку параметров и expressions, а также синтаксис сгенерированных модулей, не сохраняя проект. Проверка продолжается по workflow, чтобы показать накопленные ошибки; блокирующие ошибки дают ненулевой код завершения.

<code>convert</code> записывает проект, даже если часть workflow не переносится. Для поддерживаемого типа с неизвестным параметром, режимом или expression создаётся TODO-функция, которая завершится с <code>NotImplementedError</code>, пока её не реализуют вручную. Неизвестный тип узла или соединения и отсутствующая обязательная ссылка на credentials могут привести к пропуску узла; итоговые пропуски и упрощённые связи перечисляются в generated README и <code>conversion_report.md</code>.

Триггеры не исполняются и не входят в граф ручного запуска; проверка не требует ставить их первыми. Их имя, тип и параметры можно посмотреть в README сгенерированного проекта.

Если узлу нужны credentials, сначала укажите в workflow ссылку на credential правильного типа. <code>validate</code> сообщит об отсутствующей обязательной ссылке; после конвертации задайте реальные секреты в переменных из generated <code>config.py</code>. Секреты из n8n не копируются.

Короткий wrapper конвертации:

    uv run python convert.py workflow.json --out generated

После конвертации проверьте <code>conversion_report.md</code> и README сгенерированного проекта. Упрощённые связи через пропущенные узлы могут изменить данные, поступающие в соседние шаги.

## Запуск generated-проекта

Из каталога результата установите зафиксированные зависимости и запустите точку входа:

    cd generated
    uv sync --locked
    uv run python main.py

Такие команды работают на Windows и Linux/macOS. Wrapper <code>run.bat</code> предназначен для Windows, <code>sh run.sh</code> — для Linux/macOS.

Секреты задаются переменными окружения. Файл <code>.env</code> автоматически не загружается. В сгенерированном <code>config.py</code> указаны все переменные, которые нужны конкретному workflow.

В обычном синхронном скрипте используйте <code>from main import run</code>. В async-приложении импортируйте <code>run_async</code> и ожидайте его через <code>await</code>. В качестве входа передавайте список items, например:

    from main import run

    result = run([{"json": {"id": 1}}])

Async-вариант:

    from main import run_async

    result = await run_async([{"json": {"id": 1}}])

## Тесты

Unit и mock-интеграционные тесты запускаются командой:

    uv run pytest

Тесты HTTP, AI Chat Completions и SMTP используют mock-ответы; они не отправляют реальные письма и не требуют реального API.

Интеграционный тест PostgreSQL пропускается без <code>N8N_TEST_POSTGRES_PORT</code>. Он подключается к базе <code>workflow_test</code> от имени <code>postgres</code> с локальным тестовым паролем из тестовой конфигурации. Используйте только изолированную локальную базу.

PowerShell:

    $env:N8N_TEST_POSTGRES_PORT = "5432"
    uv run pytest tests/test_postgres_integration.py -q

Linux/macOS:

    N8N_TEST_POSTGRES_PORT=5432 uv run pytest tests/test_postgres_integration.py -q

## Изменение поддержки узлов

Конвертер узла находится в <code>src/n8n_transcriper/converters/</code>. Генератор выбирает converter по типу узла в <code>generator.py</code>; проверки workflow и CLI находятся в <code>reader.py</code>, <code>graph.py</code> и <code>cli.py</code>. Список реализованных параметров и ограничений ведётся в [справочнике узлов](supported-nodes.md).

При изменении поддержки обновляйте документацию и проверки на соответствующий converter. Неподдерживаемые параметры должны давать конкретную диагностику с именем узла и путём параметра, а не незаметно теряться.
