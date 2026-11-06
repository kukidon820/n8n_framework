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
    uv run n8n-transcriper validate workflow.json --langgraph
    uv run n8n-transcriper convert workflow.json --out generated --langgraph

<code>inspect</code> читает workflow и выводит его узлы. <code>validate</code> проверяет JSON, граф, поддержку параметров и expressions, а также синтаксис сгенерированных модулей, не сохраняя проект. Проверка продолжается по workflow, чтобы показать накопленные ошибки; блокирующие ошибки дают ненулевой код завершения.

<code>convert</code> записывает проект, даже если часть workflow не переносится. Для поддерживаемого типа с неизвестным параметром, режимом или expression создаётся TODO-функция, которая завершится с <code>NotImplementedError</code>, пока её не реализуют вручную. Неизвестный тип узла или соединения и отсутствующая обязательная ссылка на credentials могут привести к пропуску узла; итоговые пропуски и упрощённые связи перечисляются в generated README и <code>conversion_report.md</code>.

Флаг <code>--langgraph</code> задаётся отдельно для <code>convert</code> и <code>validate</code>. В обычном режиме HTTP Request всегда конвертируется как HTTP-узел; только с этим флагом детектор может заменить подходящий POST JSON-запрос к модели на LangGraph-агента. Режим добавляет нужные LangChain-пакеты только в generated-проект. Детектор провайдеров и точные ограничения приведены в [справочнике](supported-nodes.md#http-запросы-к-модели-в-режиме-langgraph).

Триггеры не исполняются и не входят в граф ручного запуска; проверка не требует ставить их первыми. Их имя, тип и параметры можно посмотреть в README сгенерированного проекта.

Если узлу нужны credentials, сначала укажите в workflow ссылку на credential правильного типа. <code>validate</code> сообщит об отсутствующей обязательной ссылке; после конвертации задайте реальные секреты в переменных из generated <code>config.py</code>. Секреты из n8n не копируются.

Короткий wrapper конвертации:

    uv run python convert.py workflow.json --out generated

После конвертации проверьте <code>conversion_report.md</code> и README сгенерированного проекта. Упрощённые связи через пропущенные узлы могут изменить данные, поступающие в соседние шаги.

Конвертер пишет этапы работы и итог по узлам в консоль. Generated <code>main.py</code> сообщает о начале и завершении workflow и узлов; при ошибке пишет имя узла и исходную причину, затем передаёт исключение вызывающему коду.

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

## Добавление AI-провайдера

Распознавание HTTP-вызовов моделей и создание агентов включены только с флагом <code>--langgraph</code>. Обычный режим конвертации менять для нового провайдера не нужно.

### Быстрый путь: OpenAI-совместимый API

Если провайдер принимает Chat Completions запросы OpenAI, отдельный адаптер обычно не нужен. В n8n задайте HTTP Request с методом <code>POST</code>, JSON-телом с полем <code>model</code>, массивом <code>messages</code> с ролями и хотя бы одним параметром генерации, например <code>temperature</code> или <code>max_tokens</code>. Для собственного домена детектор распознаёт такую структуру независимо от URL. Ответ должен содержать <code>choices[0].message.content</code>. При конвертации с <code>--langgraph</code> URL и ключ попадут в настройки generated-проекта; секрет задавайте через его config или переменную окружения.

Проверьте такой workflow командой <code>uv run n8n-transcriper validate workflow.json --langgraph</code>. Если используется n8n AI Agent, подключите модель с OpenAI-совместимыми настройками API URL, model и ключа. Поддерживаются только поля запроса и ответа, указанные в [разделе про HTTP-модели](supported-nodes.md#http-запросы-к-модели-в-режиме-langgraph).

### Полноценная поддержка собственного API

Если формат запроса или ответа отличается от существующих адаптеров, добавьте провайдера в LangGraph-часть:

1. В <code>src/n8n_transcriper/langgraph_mode/providers.py</code> добавьте <code>ProviderEndpoint</code> с точными host, путём и названием операции. Совпадение должно учитывать host и путь, а не только похожий фрагмент URL.
2. В <code>detection.py</code> опишите признаки JSON-тела для этой операции. Для известного endpoint проверьте, что структура соответствует API. Для произвольного endpoint требуйте сочетание признаков модели, prompt/messages и параметров генерации; одного массива <code>messages</code> недостаточно.
3. В <code>agent_generation.py</code> добавьте имя пакета в <code>_MODEL_PACKAGES</code> и класс модели в <code>_MODEL_CLASSES</code>. При необходимости задайте базовый URL в <code>_PROVIDER_DEFAULTS</code>, способ создать модель в <code>_model_constructor</code>, допустимые параметры в <code>_model_options</code>, заголовок авторизации и разбор ответа в <code>_response_body</code>. Для нестандартной операции используйте отдельный генератор адаптера, как для Gemini Interactions.
4. Для узла AI Model проверьте выбор провайдера в <code>_provider_for_model</code>. Добавьте соответствие пакету зависимости в <code>src/n8n_transcriper/generator.py::_langchain_package</code>, если для нового провайдера нужен новый LangChain-пакет. Не записывайте реальные ключи в сгенерированный код: генерируйте конфигурацию или переменные окружения.
5. Добавьте тесты обнаружения в <code>tests/test_langgraph_detection.py</code>, генерации HTTP-адаптера в <code>tests/test_langgraph_generation.py</code> и n8n AI Agent/Model в <code>tests/test_langgraph_ai_agents.py</code>. Проверьте и положительный пример, и похожее обычное API, которое не должно быть принято за AI.
6. Обновите [справочник узлов](supported-nodes.md) и [ограничения](limitations.md), затем запустите <code>uv run pytest</code>.

Повторно используйте существующий адаптер, если протокол и формат ответа совпадают. Новый endpoint с тем же протоколом требует регистрации адреса и проверок тела; новая схема API требует отдельной генерации, настройки авторизации, параметров и разбора ответа.
