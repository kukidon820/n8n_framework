# Поддерживаемые узлы и ограничения

Конвертер поддерживает перечисленные ниже операции. Настройки вне этого набора отклоняются с указанием узла, чтобы не терять поведение незаметно.

## Python Code

Поддерживаются Python Code Node и режимы `runOnceForAllItems` и `runOnceForEachItem`. Код берётся из `pythonCode` и помещается внутрь функции. JavaScript Code Node и неизвестный контекст исполнения не эмулируются.

## HTTP Request

Каждый узел создаёт отдельную HTTP-функцию. Реализованы основные HTTP-методы; `COPY`, `MKCOL`, `MOVE`, `PROPFIND` и `REPORT` разрешаются только при `webdavMethods: true`. Поддерживаются query-параметры, заголовки и JSON, form-urlencoded, multipart, raw и binary body.

Учитываются timeout, redirects, lowercase headers, ignore SSL, proxy без встроенных credentials, batching, retry, JSON/text/file/autodetect response, full response и never error в поддерживаемых сочетаниях. Пагинация ограничена GET-запросами с JSON-ответом и режимами:

- `updateAParameterInEachRequest` с `$pageCount` или `$pageCount + 1` и остановкой на пустом массиве;
- `responseContainsNextURL` с выражением `nextURL` и условием остановки по тому же полю ответа.

Поддерживаются generic credentials Basic, Bearer, Header и Query, а также Gmail OAuth2. Credentials хранятся в `config.py` через переменные окружения. При редиректе на другой origin они не передаются, если `sendCredentialsOnCrossOriginRedirect` явно не включён.

Другие predefined credential types, generic OAuth2/OAuth1 и digest auth требуют отдельной реализации. Секреты в статических параметрах заголовков не принимаются как способ авторизации.

## Файлы

- `convertToFile`: операция `toText`, кодировка, имя файла и UTF-8 BOM.
- `compression`: ZIP `compress` и `decompress`; архив содержит реальные байты файлов.
- `extractFromFile`: `text` и `binaryToPropery` с настройкой сохранения исходного binary.

Неподдерживаемая операция не выводится из имени узла: она приводит к диагностике.

## Edit Fields, IF и Switch

Edit Fields поддерживает ручной режим, assignments примитивных и JSON-типов, простые имена полей, сохранение/выбор JSON-полей и бинарных данных в поддерживаемых версиях Set.

IF поддерживает проверку строк, чисел и boolean. Switch поддерживает правила в режиме `rules`, fallback в существующий или дополнительный выход и маршрутизацию по всем совпавшим правилам, когда это включено.

## Merge

Реализованы только режимы Merge `Append` и `Combine`.

Combine поддерживает сопоставление по позиции, все пары и заданным полям; для полей доступны поддерживаемые join modes, fuzzy compare, обработка нескольких совпадений и настройки конфликтов. `.item` проходит через Merge, если происхождение однозначно; неоднозначная связь завершается явной ошибкой.

Другие режимы Merge не добавляются автоматически.

## AI и PostgreSQL

AI Agent поддерживается как обычный запрос к подключённой модели DeepSeek, когда у агента нет инструментов, циклов или другой агентной логики. Используются system prompt, user prompt, настройки модели и ключ `DEEPSEEK_API_KEY`.

PostgreSQL поддерживает `executeQuery` с простым SELECT/INSERT/UPDATE/DELETE, параметрами `$1...` из expressions, временем подключения и режимом независимой или транзакционной обработки items. Подключение читает `POSTGRES_*` из окружения; SQL-параметры передаются драйверу отдельно от текста запроса.

## Expressions и граф

Поддерживаются ссылки на `$json`, `$binary`, `$execution`, `$input.first()/all()` и результаты `$('Имя узла').first()/all()/item`. Также поддерживаются простые свойства/индексы, шаблоны `{{ ... }}`, логические операции, сравнения, простые литералы, массивы/объекты, строковая конкатенация и `JSON.stringify`.

Это ограниченное подмножество JavaScript, не полный интерпретатор. Неподдерживаемые функции и конструкции сообщают имя узла, параметр и исходное выражение.
