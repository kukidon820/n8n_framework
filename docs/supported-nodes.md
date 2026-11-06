# Поддерживаемые узлы и параметры

В этом справочнике перечислены параметры, которые конвертер действительно читает и преобразует. Поля вне списка не считаются поддержанными только потому, что их принимает интерфейс n8n. Если конвертер не может реализовать параметр поддерживаемого типа, он отмечает это в отчёте и оставляет TODO-заготовку; некоторые неподдерживаемые настройки исполнения, credentials и структуры графа приводят к пропуску узла. Точный результат каждого случая описан в [ограничениях](limitations.md).

Поддерживаемые типы можно добавлять в workflow в любом количестве, включая несколько экземпляров одного типа. Для Loop поддерживаются последовательные и вложенные циклы; структурные требования относятся к каждому отдельному циклу и приведены ниже.

## Какие узлы и поля выбирать в интерфейсе n8n

Таблица ниже даёт короткий ориентир по названиям полей, которые пользователь видит в редакторе n8n. Под ней для каждого узла приведены точные пути параметров из экспортированного workflow JSON и допустимые значения. Надписи интерфейса могут немного отличаться между версиями n8n, поэтому JSON-путь в подробном разделе — точный идентификатор настройки для конвертера.

| Узел в интерфейсе n8n | Тип узла в JSON | Что заполнять в интерфейсе и какие варианты поддерживаются |
|---|---|---|
| **Code** | <code>n8n-nodes-base.code</code> | **Language** — Python/Python Native; **Mode** — Run Once for All Items или Run Once for Each Item; поле Python Code. JavaScript не конвертируется. [Подробности](#python-code). |
| **HTTP Request** | <code>n8n-nodes-base.httpRequest</code> | **Method**, **URL**, **Send Query Parameters**, **Send Headers**, **Send Body**, **Authentication**; в Options — Response, Timeout, Redirects, Proxy, Batching, Pagination и перечисленные ниже дополнительные настройки. [Подробности](#http-request). |
| **Send Email** | <code>n8n-nodes-base.emailSend</code> | **From Email**, **To Email**, **Subject**, **Email Format** (Text/HTML/Both), соответствующее поле **Text** или **HTML**, а также поддержанные поля **Options**: CC, BCC, Reply-To, вложения, attribution. Нужны SMTP credentials. [Подробности](#send-email). |
| **Convert to File** | <code>n8n-nodes-base.convertToFile</code> | **Operation**: CSV, HTML, RTF, ODS, XLS, XLSX, JSON, Text, Binary или iCalendar. Для выбранной операции доступны только перечисленные ниже поля имени файла, источника, кодировки, BOM и формата. [Подробности](#convert-to-file). |
| **Compression** | <code>n8n-nodes-base.compression</code> | **Operation**: Compress или Decompress (ZIP). Для сжатия задайте имя выходного ZIP и binary-поле результата; для распаковки — binary-поле входного ZIP. [Подробности](#compression). |
| **Extract From File** | <code>n8n-nodes-base.extractFromFile</code> | **Operation**: Text или Binary to Property; задайте входное binary-поле и имя поля результата. Для текста доступны Encoding и Strip BOM; для binary — Keep Source. [Подробности](#extract-from-file). |
| **Edit Fields (Set)** | <code>n8n-nodes-base.set</code> | **Mode**: Manual Mapping; задайте **Fields to Set** (имя, тип, значение). Доступны **Include Other Input Fields**, выбор Include и опции **Strip Binary Data**, **Ignore Type Conversion Errors**, **Dot Notation**. [Подробности](#edit-fields-set). |
| **No Operation, do nothing** | <code>n8n-nodes-base.noOp</code> | Дополнительных параметров нет: входные items передаются дальше. [Подробности](#no-operation-do-nothing). |
| **IF** | <code>n8n-nodes-base.if</code> | Заполните **Conditions**: String, Number или Boolean и поддержанный оператор; объединение условий — AND/OR. Другие IF Options не задавайте. [Подробности](#if-и-switch). |
| **Switch** | <code>n8n-nodes-base.switch</code> | **Mode**: Rules; задайте правила и Conditions. Поддержанные Options: Fallback Output, Send data to all matching outputs и Rename Fallback Output. [Подробности](#if-и-switch). |
| **Merge** | <code>n8n-nodes-base.merge</code> | **Mode**: Append или Combine. Для Combine выберите Combine By: Position, All Possible Combinations или Matching Fields; остальные настройки должны соответствовать выбранному варианту. [Подробности](#merge). |
| **Wait** | <code>n8n-nodes-base.wait</code> | **Resume**: After Time Interval или At Specified Time. Заполните Amount + Unit либо Date and Time. Webhook/Form Resume не поддерживается. [Подробности](#wait). |
| **Loop Over Items (Split in Batches)** | <code>n8n-nodes-base.splitInBatches</code> | **Batch Size** — положительное целое; **Options → Reset** — фиксированный переключатель, не Expression. Также поддерживаются общие настройки **Execute Once** и Retry On Fail с Max Tries/Wait Between Tries. [Подробности](#loop-over-items-split-in-batches). |
| **AI Agent** | <code>@n8n/n8n-nodes-langchain.agent</code> | Заполните **Prompt (User Message)** и при необходимости **System Message**. Подключите ровно одну chat-модель; Streaming должен быть выключен. Tools, Memory и дополнительные AI-порты не поддерживаются. [Подробности](#ai-agent-и-chat-модель). |
| **Data Table** | <code>n8n-nodes-base.dataTable</code> | **Resource**: Row; **Operation**: Insert, Get, Update, Upsert или Delete (в JSON операция обычно записана как <code>deleteRows</code>). Выберите таблицу, режим сопоставления столбцов, фильтры и параметры Get/Insert, перечисленные ниже. [Подробности](#data-table). |
| **Postgres** | <code>n8n-nodes-base.postgres</code> | **Resource**: Database; **Operation**: Execute Query. Заполните Query, Query Parameters и поддержанные Options. Данные соединения задаются в конфигурации generated-проекта. [Подробности](#postgresql). |
| **Подключённая chat-модель** | Тип зависит от выбранного провайдера | Подключается к AI Agent через порт **Language Model**. Модель используется как конфигурация AI-вызова, а не как отдельный шаг. В обычном режиме применяется OpenAI-compatible Chat Completions; в режиме <code>--langgraph</code> доступны провайдерные адаптеры из раздела ниже. [Подробности](#ai-agent-и-chat-модель). |
| **Trigger / Webhook** | Разные типы, в том числе <code>n8n-nodes-base.webhook</code> | Триггеры распознаются и описываются в generated README, но не запускаются. Сгенерированный <code>main.py</code> начинает выполнение вручную. [Правила](#запуск-и-общие-настройки-узла). |

Для узлов с дополнительными настройками откройте в интерфейсе **Add Option**/**Options** и добавляйте только перечисленные ниже поля. Установка неизвестного поля не означает, что конвертер его перенесёт: неподдержанная настройка указывается в отчёте конвертации.

## Правила конвертации

### Запуск и общие настройки узла

- Trigger-узлы и Webhook не запускаются. Они не становятся функциями; generated README перечисляет имя триггера, тип и параметры. Generated <code>main.py</code> запускается вручную.
- Для обычных узлов <code>disabled</code> и <code>continueOnFail</code> поддерживаются только со значением <code>false</code>. Поддерживается только <code>onError=stopWorkflow</code>.
- <code>alwaysOutputData</code> и <code>executeOnce</code> должны быть логическими значениями. Значение <code>true</code> поддерживается для обычных операций: <code>executeOnce</code> использует первый входной item, а <code>alwaysOutputData</code> формирует пустой item при пустом результате. Для IF, Switch и Merge эти настройки со значением <code>true</code> не поддерживаются. Для Loop <code>executeOnce=true</code> оставляет только первый входной item при старте и при передаче обратной связи.
- <code>retryOnFail</code>, <code>maxTries</code> и <code>waitBetweenTries</code> реализованы для HTTP Request и Loop Over Items. У HTTP повторы охватывают запрос; у Loop — внутреннюю подготовку пакетов и обратной связи, но не ошибки шагов внутри тела цикла. Для остальных узлов включённый retry не переносится автоматически; смотрите диагностику и отчёт.
- На выходе обычных функций данные имеют n8n-подобную структуру <code>{"json": {...}, "binary": {...}}</code>. Binary хранит реальные bytes, имя файла и MIME-тип.

### Асинхронное исполнение

- Generated <code>main.py</code> всегда предоставляет <code>async def run_async(...)</code>; вызовы функций узлов ожидаются через <code>await</code>.
- Все сгенерированные функции узлов асинхронные; HTTP, AI, SMTP и PostgreSQL используют нативные async-клиенты.
- Wait и Data Table используют нативные async-операции. Loop выдаёт batches как async-итератор и делает retry-паузы через <code>asyncio.sleep</code>.
- <code>run(input_data)</code> — синхронная обёртка для запуска из обычного скрипта. В async-приложениях вызывайте <code>await run_async(input_data)</code>.

### Что происходит с неподдерживаемой частью

Для поддерживаемого типа узла неизвестный параметр, режим или неподдерживаемое expression отмечается как ручная TODO-реализация. Узел остаётся в проекте, а сгенерированная функция завершится с <code>NotImplementedError</code>, пока пользователь не добавит нужную логику. Диагностика появляется в <code>main.py</code>, generated README и <code>conversion_report.md</code>.

Узлы с неизвестным типом, неподдерживаемые дополнительные соединения и узлы без обязательной ссылки на credentials могут быть исключены из сгенерированного графа. Связи через такой узел упрощаются, поэтому данные на входе соседней функции могут отличаться от данных в n8n. Проверьте комментарии и отчёт, затем при необходимости восстановите обработку вручную.

<code>validate</code> проверяет все узлы и показывает накопленные диагностики. При блокирующей проблеме, например отсутствующей обязательной ссылке на credentials, он завершается с ненулевым кодом. <code>convert</code> при этом продолжает генерацию доступной части и отчёта.

Trigger-узлы не запускаются и исключены из исполняемой логики; проверка порядка workflow не требует ставить их первыми. Их имя, тип и параметры записываются в README сгенерированного проекта; запуск workflow остаётся ручным из <code>main.py</code>.

### Версии узлов

Версия сама по себе не блокирует генерацию. Для типов узлов с известными version-sensitive различиями появляется предупреждение и выбирается fallback для настроек, отсутствующих в JSON. Уже присутствующие и распознанные параметры читаются по имени и проверяются по типу, даже если они пришли из другой версии узла. Если конвертер встречает неизвестный параметр, режим или неподдерживаемое expression у любого поддерживаемого типа, узел остаётся в проекте как отдельная TODO-заготовка; неизвестная часть отмечается в `main.py`, README и отчёте. Такая функция завершится с `NotImplementedError`, пока пользователь не реализует поведение вручную. В generated-код передаётся <code>n8n_manual_parameters</code> с исходной версией и шаблоном структуры параметров; значения и секреты из workflow не копируются.

Проверенные диапазоны и правила fallback:

| Тип узла | Проверенный формат | Правило за пределами диапазона |
|---|---|---|
| Merge | 3.x | Используется fallback 3 |
| PostgreSQL | 2.8 и новее | Для более старой или некорректной версии используется 2.8 |
| Edit Fields / Set | 3.3–3.5 | Используется ближайшая граница 3.3 или 3.5 |
| No Operation | 1 | Используется fallback 1 |
| Loop Over Items | основные версии 1, 2 и 3 | Используется ближайшая поддерживаемая основная версия |
| Data Table | 1.0–1.1 | Используется ближайшая версия 1.0 или 1.1 |
| Wait | 1.0–1.1 | Используется ближайшая версия 1.0 или 1.1 |
| HTTP Request | 4.0–4.5 | Для более старой версии используется 4.0, для более новой — 4.5 |
| Send Email | 2.0–2.1 | Для более старой версии используется 2.0, для более новой — 2.1 |
| Convert to File | 1.0–1.2 | Используется ближайшая граница диапазона |
| Compression | 1.0–1.1 | Используется ближайшая граница диапазона |
| Extract From File | 1 | Используется fallback 1 |

Если у HTTP Request, Send Email или файлового узла версия отсутствует, используется версия по умолчанию конвертера без отдельного предупреждения.

### Credentials и секреты

Credential в workflow задаёт ссылку и тип авторизации, но не даёт конвертеру секретное значение. Если узлу требуется credential, а ссылка на него отсутствует, <code>validate</code> сообщает ошибку; при <code>convert</code> затронутый узел исключается из сгенерированной части. В частности, SMTP credentials нужны для Send Email, а PostgreSQL credentials — для SQL-узла. Data Table использует отдельные параметры подключения PostgreSQL в generated <code>config.py</code>.

Когда ссылка присутствует, сгенерированный конфиг перечисляет необходимые переменные окружения, но сами значения нужно задать вручную. Секреты из n8n не копируются.

## Python Code

Тип: <code>n8n-nodes-base.code</code>.

| Параметр | Значения | Поведение |
|---|---|---|
| <code>language</code> | <code>python</code>, <code>pythonNative</code> | Выбирает Python-реализацию |
| <code>pythonCode</code> | Непустой исходный код | Помещается в generated-функцию практически без изменения алгоритма |
| <code>mode</code> | <code>runOnceForAllItems</code>, <code>runOnceForEachItem</code> | Передаёт весь список items или запускает функцию отдельно для каждого item |

JavaScript Code Node не исполняется и не переписывается. Если код ссылается на неподдерживаемый контекст, конвертер сообщает имя узла и исключает его из partial-конвертации.

## HTTP Request

Тип: <code>n8n-nodes-base.httpRequest</code>. Для каждого узла генерируется отдельная функция на <code>httpx</code>.

### Основные параметры

| Параметр | Поддержка |
|---|---|
| <code>method</code> | <code>DELETE</code>, <code>GET</code>, <code>HEAD</code>, <code>OPTIONS</code>, <code>PATCH</code>, <code>POST</code>, <code>PUT</code>. Методы <code>COPY</code>, <code>MKCOL</code>, <code>MOVE</code>, <code>PROPFIND</code> и <code>REPORT</code> доступны только с <code>options.webdavMethods=true</code>. |
| <code>url</code> | Обязательный URL; поддерживаются статические значения и expressions. |
| <code>sendQuery</code>, <code>queryParameters.parameters</code> | Пары <code>name</code>/<code>value</code>; значения могут быть expressions. |
| <code>sendHeaders</code>, <code>headerParameters.parameters</code> | Пары <code>name</code>/<code>value</code>. Заголовки читаются независимо от наличия credentials. |
| <code>sendBody</code> | Включает передачу тела. Если выключен, параметры тела задавать нельзя. |
| <code>specifyBody</code>, <code>jsonBody</code> | Для JSON поддерживается полный JSON/expression в режиме <code>json</code>. |
| <code>contentType</code>, <code>bodyParameters</code> | Режимы JSON key/value, <code>form-urlencoded</code> и <code>multipart-form-data</code>. |
| <code>bodyParameters.parameters</code> | Текстовая пара <code>name</code>/<code>value</code>; multipart также поддерживает <code>parameterType=formBinaryData</code> с <code>inputDataFieldName</code>. |
| <code>body</code>, <code>rawContentType</code> | Тело <code>raw</code>; expression тела кодируется в UTF-8. |
| <code>inputDataFieldName</code> | Binary-поле текущего item для <code>binaryData</code> или multipart-вложения. |
| <code>authentication</code> | <code>none</code>, поддерживаемый generic credential либо Gmail OAuth2. |
| <code>genericAuthType</code> | <code>httpBasicAuth</code>, <code>httpBearerAuth</code>, <code>httpHeaderAuth</code>, <code>httpQueryAuth</code>. Для generic credentials требуется ссылка на credential ID в JSON; секрет задаётся в generated <code>config.py</code>. |
| <code>nodeCredentialType</code> | Поддерживается <code>gmailOAuth2</code> при <code>authentication=predefinedCredentialType</code>. |
| <code>retryOnFail</code>, <code>maxTries</code>, <code>waitBetweenTries</code> | Retry по HTTP-ошибке; число попыток и задержка между ними. Задержка задаётся в миллисекундах. |

Имя и значение обычного заголовка переносятся как есть. Заголовки со статическими значениями, похожими на секреты (Authorization, token, password, cookie, api key и др.), выносятся в отдельные переменные окружения и <code>config.py</code>. Expressions не угадываются как секреты, потому что вычисляются из данных workflow.

### HTTP options

| Путь параметра | Значение по умолчанию | Поведение |
|---|---:|---|
| <code>options.timeout</code> | 300000 мс | Таймаут HTTP-клиента; в generated-коде переводится в секунды |
| <code>options.response.responseFormat</code> | <code>autodetect</code> | <code>json</code>, <code>text</code>, <code>file</code> или <code>autodetect</code> |
| <code>options.response.outputPropertyName</code> | <code>data</code> | Имя поля JSON для текстового ответа или binary-поля для файла; допускается одно имя без точек |
| <code>options.response.fullResponse</code> | <code>false</code> | Добавляет заголовки, код и текст статуса к JSON/text ответу |
| <code>options.response.neverError</code> | <code>false</code> | Не вызывает ошибку на HTTP status error; несовместимо с pagination |
| <code>options.allowUnauthorizedCerts</code> | <code>false</code> | Отключает проверку TLS-сертификата только при явном включении |
| <code>options.redirect.redirect.followRedirects</code> | Зависит от версии узла | Разрешает перенаправления |
| <code>options.redirect.redirect.maxRedirects</code> | 21 | Максимальное количество перенаправлений |
| <code>options.proxy</code> | Не задан | URL прокси; proxy с логином или паролем в URL не поддерживается |
| <code>options.lowercaseHeaders</code> | <code>true</code> | Приводит имена заголовков к нижнему регистру |
| <code>options.sendCredentialsOnCrossOriginRedirect</code> | <code>false</code> | По умолчанию секретные заголовки и credentials не отправляются на другой origin при redirect |
| <code>options.webdavMethods</code> | <code>false</code> | Разрешает методы WebDAV, перечисленные выше |
| <code>options.batching.batch.batchSize</code> | 50 | Размер пачки входных items; <code>-1</code> отключает ограничение размера |
| <code>options.batching.batch.batchInterval</code> | 1000 мс | Пауза между пачками; значение 0 не добавляет задержку |

### HTTP pagination

Поддерживаются только GET без тела и JSON-ответ. <code>limitPagesFetched=true</code> обязателен; <code>maxRequests</code> задаёт положительный предел страниц, <code>requestInterval</code> — паузу в миллисекундах. Сочетание pagination с <code>fullResponse</code> или <code>neverError</code> не поддерживается.

- <code>updateAParameterInEachRequest</code>: один query-параметр типа <code>qs</code>; значение — только <code>$pageCount</code> или <code>$pageCount + 1</code>. Завершение — <code>paginationCompleteWhen=responseIsEmpty</code>; ответ должен быть JSON-массивом.
- <code>responseContainsNextURL</code>: только без авторизации, с <code>paginationCompleteWhen=other</code>. <code>nextURL</code> должен указывать на поле в <code>$response.body</code>, а <code>completeExpression</code> — проверять отсутствие того же поля. URL следующей страницы должен быть абсолютным HTTP/HTTPS URL.

Gmail API credentials поддерживаются для OAuth2. Запрос на <code>https://gmail.googleapis.com/gmail/v1/users/me/messages/send</code> преобразуется в SMTP-отправитель raw MIME-сообщения. Остальные Gmail API-запросы остаются HTTP. OAuth1, Digest и другие generic/predefined типы требуют отдельного адаптера.

## Send Email

Тип: <code>n8n-nodes-base.emailSend</code>. Узел формирует MIME-письмо и отправляет его по SMTP. В workflow должна присутствовать ссылка на SMTP credentials; имя пользователя и пароль задаются пользователем.

| Параметр | Поддержка |
|---|---|
| <code>emailFormat</code> | <code>text</code>, <code>html</code>, <code>both</code> |
| <code>fromEmail</code>, <code>toEmail</code> | Отправитель и один или несколько получателей |
| <code>subject</code> | Тема письма |
| <code>text</code>, <code>html</code> | Текстовая и HTML-части; поддерживают компилируемые expressions |
| <code>options.ccEmail</code>, <code>options.bccEmail</code>, <code>options.replyTo</code> | Копия, скрытая копия и адрес ответа |
| <code>options.attachments</code>, <code>options.fileAttachments</code> | Имена binary-полей для файловых вложений |
| <code>options.appendAttribution</code> | Добавление подписи n8n |
| <code>options.allowUnauthorizedCerts</code> | Разрешает SMTP TLS-сертификат без проверки только при явном включении |

Generated <code>config.py</code> использует SMTP_HOST, SMTP_PORT (по умолчанию 587), SMTP_USERNAME, SMTP_APP_PASSWORD, SMTP_FROM, SMTP_HOSTNAME, SMTP_USE_SSL и SMTP_STARTTLS. Должен быть включён SSL или STARTTLS. Для Gmail используйте пароль приложения. Секреты исходного credential не копируются.

Если <code>emailFormat</code> не задан, до версии 2 используется <code>html</code>, начиная с версии 2 — <code>text</code>. <code>options.appendAttribution</code> по умолчанию включён для версии 2.1 и новее.

## Convert to File

Тип: <code>n8n-nodes-base.convertToFile</code>. Если <code>operation</code> отсутствует, используется n8n default <code>csv</code>. Для большинства операций результат записывается в binary-поле <code>binaryPropertyName</code> (по умолчанию <code>data</code>).

### Табличные форматы

Операции: <code>csv</code>, <code>html</code>, <code>rtf</code>, <code>ods</code>, <code>xls</code>, <code>xlsx</code>. Все items сворачиваются в одну таблицу; поля JSON становятся столбцами, а разные наборы полей объединяются.

Поддерживаются:

- <code>options.fileName</code> — имя файла;
- <code>options.headerRow</code> — добавлять ли строку заголовков, по умолчанию <code>true</code>;
- <code>options.delimiter</code> — один символ-разделитель, только для CSV;
- <code>options.sheetName</code> — имя листа для ODS/XLS/XLSX;
- <code>options.compression</code> — сжатие для ODS/XLSX.

### JSON-файл

Операция <code>toJson</code> принимает <code>mode=once</code> или <code>each</code>. Поддерживаются <code>options.addBOM</code>, <code>options.encoding</code>, <code>options.fileName</code>, <code>options.format</code> (pretty-print как boolean) и <code>binaryPropertyName</code>. В режиме once создаётся один файл со списком JSON всех входных items; в each — файл для каждого item.

### Текстовый и binary-файл

- <code>toText</code>: обязательны <code>sourceProperty</code>, <code>options.encoding</code>, <code>options.addBOM</code>, <code>options.fileName</code>; выходное поле задаётся <code>binaryPropertyName</code>. Если значение — объект или массив, оно сериализуется в JSON-текст.
- <code>toBinary</code>: обязательны <code>sourceProperty</code> и <code>binaryPropertyName</code>; поддерживаются <code>options.addBOM</code>, <code>options.encoding</code>, <code>options.fileName</code>, <code>options.mimeType</code>. Если задано <code>options.dataIsBase64</code>, его логическое значение определяет base64 или обычный текст независимо от версии. При отсутствии параметра используется base64; версия влияет на строгость проверки base64.

### iCalendar

Операция <code>iCal</code> создаёт файл календаря. Поддерживаются <code>title</code>, обязательный <code>start</code>, <code>end</code> (по умолчанию равен start), <code>allDay</code>, <code>binaryPropertyName</code> и следующие поля <code>additionalFields</code>: <code>attendeesUi</code>, <code>busyStatus</code>, <code>calName</code>, <code>description</code>, <code>fileName</code>, <code>geolocationUi</code>, <code>location</code>, <code>organizerUi</code>, <code>recurrenceRule</code>, <code>sequence</code>, <code>status</code>, <code>uid</code>, <code>url</code>, <code>useWorkflowTimezone</code>. Если <code>useWorkflowTimezone=true</code>, workflow должен задавать timezone. Значения iCalendar сейчас встраиваются при генерации, expressions для этих полей не вычисляются.

## Compression

Тип: <code>n8n-nodes-base.compression</code>. Реализованы операции ZIP <code>compress</code> и <code>decompress</code>.

- <code>compress</code>: для каждого входного item его binary-файлы упаковываются в настоящий ZIP. Поддерживаются <code>fileName</code> (по умолчанию <code>archive.zip</code>), <code>binaryPropertyOutput</code> (по умолчанию <code>data</code>) и пустой объект <code>options</code>.
- <code>decompress</code>: ZIP берётся из <code>binaryPropertyName</code> (по умолчанию <code>data</code>). Каждый файл становится binary-полем <code>file_0</code>, <code>file_1</code> и далее; каталоги пропускаются. Поддерживается пустой <code>options</code>.
- Если <code>operation</code> отсутствует, используется <code>decompress</code>. Параметры <code>outputFormat</code> и <code>outputPrefix</code> не поддерживаются.

## Extract From File

Тип: <code>n8n-nodes-base.extractFromFile</code>. Общие параметры — <code>binaryPropertyName</code> (по умолчанию <code>data</code>) и <code>destinationKey</code> (по умолчанию <code>data</code>).

- <code>operation=text</code>: читает байты binary-поля, декодирует через <code>options.encoding</code> (по умолчанию UTF-8) и, если задано <code>options.stripBOM=true</code>, удаляет BOM. Текст записывается в destinationKey.
- <code>operation=binaryToPropery</code>: n8n использует именно написание <code>binaryToPropery</code>. Содержимое binary записывается в destinationKey как base64. <code>options.keepSource</code> принимает <code>none</code> (по умолчанию), <code>json</code>, <code>binary</code> или <code>both</code>.
- Извлечение CSV и другие операции не входят в поддержку.

## Edit Fields (Set)

Тип: <code>n8n-nodes-base.set</code>. Реализован режим <code>mode=manual</code>. Версия влияет на defaults и fallback, но известные параметры обрабатываются независимо от версии.

- <code>assignments.assignments</code>: список полей с <code>name</code>, <code>value</code>, <code>type</code> и необязательным <code>id</code>. Поддерживаемые типы: <code>string</code>, <code>number</code>, <code>boolean</code>, <code>object</code>, <code>array</code>. <code>value</code> может содержать поддерживаемое expression.
- <code>includeOtherFields</code> — Include Other Input: копировать ли исходный JSON в результат.
- <code>include</code>: <code>all</code>, <code>none</code>, <code>selected</code> или <code>except</code>. Списки задаются статическими именами полей. Динамический список и сложные пути в выборе полей не поддерживаются.
- Если задано <code>includeOtherFields</code>, оно обрабатывается независимо от версии. Если его нет, используется явно заданное <code>include</code>; версия влияет только на выбор значения по умолчанию.
- <code>options.stripBinary</code> — Strip Binary Data; при явном наличии обрабатывается независимо от версии.
- <code>options.includeBinary</code> — старый вариант управления binary; при явном наличии тоже обрабатывается независимо от версии. Если заданы оба варианта, версия помогает выбрать активный ключ; несовпадение версии само по себе не отклоняет узел.
- <code>options.ignoreConversionErrors</code> — Ignore Type Conversion Errors: при ошибке приведения сохраняет исходное значение, иначе ошибка прерывает работу generated-функции.
- <code>options.dotNotation</code> по умолчанию включён. При включённом режиме неоднозначные имена с точками или скобками отклоняются; при выключенном имя считается буквальным.
- Настройки задаются в <code>options</code>; неизвестные поля и режимы Set приводят к TODO-заготовке вместо автоматического выполнения.

## No Operation (Do Nothing)

Тип: <code>n8n-nodes-base.noOp</code>. Узел не принимает дополнительных параметров и передаёт входные items дальше без изменений. Проверенный типVersion — 1.

## IF и Switch

Оба узла принимают поддерживаемые условия из <code>conditions</code>. В условии используются <code>leftValue</code>, <code>rightValue</code> и <code>operator</code>; идентификатор условия сохраняется только как n8n-метаданные.

### Общие поля условия

- <code>combinator</code>: <code>and</code> или <code>or</code>.
- <code>options.caseSensitive</code>: учитывать регистр при строковом сравнении.
- <code>options.leftValue</code> и <code>options.version</code>: принимаются как метаданные формата n8n.
- <code>options.typeValidation</code>: поддерживается только <code>strict</code>.
- Параметры оператора: <code>type</code>, <code>operation</code> и <code>singleValue</code>.
- Строковые операторы: <code>equals</code>, <code>notEquals</code>, <code>contains</code>, <code>notContains</code>, <code>empty</code>, <code>notEmpty</code>.
- Числовые операторы: <code>equals</code>, <code>notEquals</code>, <code>gt</code>, <code>gte</code>, <code>lt</code>, <code>lte</code>.
- Boolean-операторы: <code>true</code>, <code>false</code>, <code>equals</code>, <code>notEquals</code>.

### IF

Тип: <code>n8n-nodes-base.if</code>. Принимает <code>conditions</code> и пустой <code>options</code>. Items направляются в true-выход 0 либо false-выход 1. Дополнительные IF options не поддерживаются.

### Switch

Тип: <code>n8n-nodes-base.switch</code>. Поддерживается <code>mode=rules</code> и непустой список <code>rules.values</code>. У правила поддерживаются <code>conditions</code>, <code>renameOutput</code> и <code>outputKey</code>. <code>renameOutput</code>/<code>outputKey</code> описывают названия выходов и не меняют сравнение условий.

Из <code>options</code> поддерживаются:

- <code>fallbackOutput</code>: <code>none</code>, <code>extra</code> или индекс существующего выхода;
- <code>allMatchingOutputs</code>: отправлять item во все совпавшие правила или только в первое;
- <code>renameFallbackOutput</code>: имя fallback-выхода, только для отображения.

## Merge

Тип: <code>n8n-nodes-base.merge</code>. Реализованы только режимы <code>append</code> и <code>combine</code>; значения <code>chooseBranch</code>, SQL и другие режимы не поддерживаются.

### Append

Поддерживаются <code>mode</code>, <code>numberInputs</code> и пустой <code>options</code>. Число входов — от 2 до 10. Items добавляются подряд по порядку входных портов.

### Combine

Поддерживаются <code>combineBy</code>, <code>numberInputs</code> и параметры, применимые к выбранному варианту:

- <code>combineBy=combineByPosition</code>: items попарно соединяются по индексу. С <code>options.includeUnpaired=true</code> более длинные входы дополняют результат непарными items.
- <code>combineBy=combineAll</code>: декартово произведение двух входов.
- <code>combineBy=combineByFields</code>: сопоставление двух входов по полям. Пары задаются через <code>fieldsToMatchString</code> (одни и те же имена) либо при <code>advanced=true</code> через <code>mergeByFields.values</code> с парами <code>field1</code>/<code>field2</code>.

Для Combine поддерживаются:

- <code>joinMode</code>: <code>keepMatches</code>, <code>keepNonMatches</code>, <code>keepEverything</code>, <code>enrichInput1</code>, <code>enrichInput2</code>;
- <code>outputDataFrom</code>: <code>both</code>, <code>input1</code> или <code>input2</code>; применяется к <code>keepMatches</code> и <code>keepNonMatches</code>;
- <code>options.clashHandling.values.resolveClash</code>: <code>preferLast</code>, <code>addSuffix</code> или <code>preferInputN</code>;
- <code>options.clashHandling.values.mergeMode</code>: <code>deepMerge</code> или <code>shallowMerge</code>;
- <code>options.clashHandling.values.overrideEmpty</code>: включать ли пустые значения при слиянии;
- <code>options.multipleMatches</code>: <code>all</code> или <code>first</code>;
- <code>options.disableDotNotation</code>: трактовать имена полей как буквальные;
- <code>options.fuzzyCompare</code>: нестрогое сравнение при поиске пары по полям.

Число входов ограничивается выбранным вариантом; Combine по всем входам и по полям требует ровно два входа. Если дополнительные параметры Combine не распознаются или не поддерживаются, узел остаётся в проекте как TODO-заготовка; проверьте диагностику перед запуском. Однозначные связи item после Merge сохраняются; неоднозначные ссылки <code>.item</code> сообщают об ошибке.

## Wait

Тип: <code>n8n-nodes-base.wait</code>. Generated функция async и сохраняет входные items без изменений.

- <code>parameters.resume=timeInterval</code>: ожидает заданный интервал через <code>asyncio.sleep</code>.
- <code>parameters.amount</code>: неотрицательное число или поддерживаемое expression; без значения используется default n8n для версии узла.
- <code>parameters.unit</code>: <code>seconds</code>, <code>minutes</code>, <code>hours</code> или <code>days</code>.
- <code>parameters.resume=specificTime</code>: ожидает до <code>parameters.dateTime</code>. Для даты без часового пояса используется локальное время процесса; timezone workflow n8n не влияет на ожидание.
- Expressions в <code>amount</code> и <code>dateTime</code> компилируются на этапе генерации; ссылки на предыдущие узлы проверяются как обычные зависимости графа.
- <code>resume=webhook</code> и <code>resume=form</code> не реализованы: они требуют сервера для внешнего возобновления и сохранения suspended execution. Для такого режима генерируется TODO-заготовка с пояснением в <code>main.py</code>, README и <code>conversion_report.md</code>.

## Loop Over Items (Split In Batches)

Тип: <code>n8n-nodes-base.splitInBatches</code>. В generated-проекте это простой класс <code>Loop</code> в <code>operations/loop.py</code>, который используется через async-итератор.

- <code>parameters.batchSize</code>: положительное целое; стандартное значение — 10 для основных версий 1 и 2, 1 для версии 3.
- <code>parameters.options.reset</code>: логическое значение; других Loop options нет.
- <code>executeOnce</code>: оставляет первый item каждого входа Loop, включая входящие данные обратной связи.
- <code>retryOnFail</code>, <code>maxTries</code>, <code>waitBetweenTries</code>: повторы внутренних операций подготовки входных items, выдачи пакета и копирования обратной связи. По умолчанию — 3 попытки с паузой 1000 мс. Повторяется ошибка самого контроллера Loop; для ошибок в узлах тела цикла настройте retry на соответствующем узле.
- Выход Loop подаёт пакет в тело, выход Done возвращает итог после обратной связи. Схема выходов учитывается по основной версии узла.
- Можно использовать любое количество Loop Over Items: последовательно, независимо или вложенно, в том числе несколько циклов в LangGraph-проекте.

Для каждого Loop требуется ровно один начальный main-вход на порт 0, подключённый выход loop и ровно одна обратная связь с выхода 0 узла внутри тела на вход 0 Loop. Тело цикла должно быть непустым.

Если подключена ветка done, она не должна пересекаться с телом цикла. Вложенный Loop должен целиком лежать внутри тела внешнего цикла; частично пересекающиеся области циклов не поддерживаются.

Обратные связи разрешены только как штатное соединение Loop. Другие циклы в main-графе, несколько входных потоков в один Loop и обратная связь из-за пределов тела приводят к ошибке структуры графа.

## AI Agent и chat-модель

Тип Agent: <code>@n8n/n8n-nodes-langchain.agent</code>. Тип модели определяется подключённым узлом; узел модели служит конфигурацией и не становится самостоятельным шагом в <code>main.py</code>.

Для Agent поддерживается:

- <code>parameters.text</code> — обязательный prompt;
- <code>parameters.options.systemMessage</code> — system prompt;
- <code>parameters.options.enableStreaming=false</code>;
- ровно одно соединение <code>ai_languageModel</code> на каждый Agent к поддерживаемому узлу chat-модели. Один узел-модель разрешается подключать к нескольким Agent.

Для модели поддерживаются:

- <code>parameters.model</code> — идентификатор модели;
- URL совместимого Chat Completions API из <code>apiUrl</code>, <code>apiURL</code>, <code>api_url</code>, <code>endpoint</code>, <code>url</code> или <code>baseUrl</code>/<code>baseURL</code>/<code>base_url</code> в параметрах или options;
- <code>apiKeyHeader</code> и <code>apiKeyPrefix</code> для заголовка ключа;
- <code>options.timeout</code> в миллисекундах (по умолчанию 180000);
- <code>options.maxRetries</code> (по умолчанию 0);
- прочие model options, если они могут быть переданы в API payload; имена camelCase преобразуются в snake_case, <code>maxTokens</code> преобразуется в <code>max_tokens</code>.

Агент без tools — обычный HTTP-запрос к OpenAI-compatible Chat Completions API. Значения URL и ключа переносятся в <code>config.py</code>, а не встраиваются в код. Поддерживаются expressions prompt из текущих и именованных узлов.

AI-соединения <code>ai_tool</code>, <code>ai_memory</code> и другие дополнительные каналы не реализованы. Если Agent подключён, например, к Think Tool через <code>ai_tool</code>, Agent будет исключён из проекта с объяснением; связанная модель также исключается, если она больше не нужна. Значит, такой Agent не исполнится в сгенерированной программе. В обычном режиме Agent вызывает подключённую модель простым async-вызовом; LangGraph используется только по флагу <code>--langgraph</code>.

### HTTP-запросы к модели в режиме LangGraph

С <code>--langgraph</code> конвертер дополнительно проверяет HTTP Request. Проверка отключена в обычном режиме, где HTTP-узел всегда остаётся обычным запросом.

Узел рассматривается как AI-вызов только для POST-запроса с JSON-телом. Для известных API проверяются и адрес, и ожидаемая форма тела. Распознаются:

| Провайдер | Адрес API |
|---|---|
| OpenAI | <code>api.openai.com/v1/chat/completions</code> |
| DeepSeek | <code>api.deepseek.com/v1/chat/completions</code> и <code>/chat/completions</code> |
| OpenRouter | <code>openrouter.ai/api/v1/chat/completions</code> или <code>api.openrouter.ai/api/v1/chat/completions</code> |
| Anthropic | <code>api.anthropic.com/v1/messages</code> |
| Google Gemini | <code>generativelanguage.googleapis.com/...:generateContent</code>, <code>:streamGenerateContent</code>, <code>/v1/interactions</code> или <code>/interactions</code> |
| Ollama | <code>/api/chat</code> и <code>/api/generate</code> на локальном сервере или порту 11434 |

Для собственного URL анализируется форма тела. Распознаются модели запроса <code>model</code> + <code>messages</code> с ролями и параметром генерации; Gemini <code>contents</code> с текстовыми parts и параметром генерации; либо <code>model</code> + <code>prompt</code> + параметр генерации. Один массив <code>messages</code> без этих признаков AI-вызовом не считается.

Распознанный запрос заменяется вызовом адаптера провайдера. Используются prompt, модель, поддержанные параметры генерации и заголовки. Настройки обычного HTTP Request — pagination, batching, timeout/retry, redirects и response format — не переносятся в AI-адаптер. Для собственного OpenAI-compatible URL ответ должен иметь структуру <code>choices[0].message.content</code>. Потоковый ответ Gemini Interactions явно требует ручной реализации; потоковый режим других адаптеров также не сохраняет семантику потоковой выдачи.

Отдельные <code>queryParameters</code> n8n не переносятся. Query-часть URL собственного endpoint сохраняется, кроме имён параметров, похожих на секреты; для известных API формируется базовый URL провайдера.

## Data Table

Тип: <code>n8n-nodes-base.dataTable</code>. Поддерживается только ресурс <code>row</code>. В интерфейсе выберите операции Insert, Get, Update, Upsert или Delete; в JSON Delete обычно экспортируется как <code>deleteRows</code>, также принимается alias <code>delete</code>.

### Таблица и значения

- <code>dataTableId</code>: ссылка на Data Table в режиме <code>list</code>, <code>id</code> или <code>name</code>. Значение может быть литералом или поддерживаемым expression. В generated <code>config.py</code> настройка <code>DATA_TABLES</code> связывает ID/имя из workflow с PostgreSQL-таблицей; таблицу можно указать как <code>table</code> или <code>schema.table</code>.
- <code>columns.mappingMode=defineBelow</code>: значения столбцов задаются в <code>columns.value</code>. Expression допускаются для скалярных значений.
- <code>columns.mappingMode=autoMapInputData</code>: берутся JSON-поля входного item.
- В записываемые данные нельзя передать системные столбцы <code>id</code>, <code>createdAt</code>, <code>updatedAt</code>. Поддерживаются скалярные значения; массивы и объекты не поддерживаются.

### Фильтры и операции

- <code>filters.conditions</code>: условия с <code>keyName</code>, <code>condition</code> и, когда нужен, <code>keyValue</code>.
- <code>matchType</code>: <code>anyCondition</code>/<code>any</code> объединяет через OR, <code>allConditions</code>/<code>all</code> — через AND.
- Условия: <code>eq</code>, <code>neq</code>, <code>gt</code>, <code>gte</code>, <code>lt</code>, <code>lte</code>, <code>like</code>, <code>ilike</code>, <code>isEmpty</code>, <code>isNotEmpty</code>, <code>isTrue</code>, <code>isFalse</code>. Пустые значения для eq/neq сравниваются с SQL NULL.
- <code>get</code>: <code>returnAll</code> (по умолчанию <code>false</code>), <code>limit</code> (по умолчанию 50), <code>orderBy</code>, <code>orderByColumn</code> (по умолчанию <code>createdAt</code>), <code>orderByDirection</code> (<code>ASC</code>/<code>DESC</code>). Вложенный <code>options</code> должен быть пустым.
- <code>insert</code>: <code>options.optimizeBulk</code> возвращает один сводный item с количеством вставленных строк вместо отдельного item на каждую строку.
- <code>update</code>, <code>upsert</code>, <code>deleteRows</code>: требуется хотя бы один фильтр. <code>options.dryRun</code> допускается только со значением <code>false</code>.
- Запросы к PostgreSQL асинхронны и используют asyncpg. Значения передаются параметрами, а имена схемы, таблицы и полей экранируются.

### Формат результата и схема PostgreSQL

Каждая возвращённая строка становится отдельным n8n item <code>{"json": row}</code>. Даты преобразуются в ISO-строки. get возвращает найденные строки, операции записи/удаления — затронутые строки. При <code>optimizeBulk=true</code> insert возвращает summary item с количеством вставок.

Внешняя таблица создаётся вручную; сами внутренние Data Tables из n8n не копируются. Для совместимого формата результата таблица должна иметь числовой генерируемый столбец <code>id</code>, <code>createdAt</code> и <code>updatedAt</code> типа TIMESTAMPTZ, а также пользовательские столбцы. insert/upsert задают даты создания и обновления; update/upsert обновляют <code>updatedAt</code>. Generated config содержит <code>POSTGRES_HOST</code>, <code>POSTGRES_PORT</code>, <code>POSTGRES_DATABASE</code>, <code>POSTGRES_USER</code>, <code>POSTGRES_PASSWORD</code>, <code>POSTGRES_CONNECT_TIMEOUT</code> и <code>DATA_TABLES</code>.

## PostgreSQL

Тип: <code>n8n-nodes-base.postgres</code>. Поддерживается только ресурс <code>database</code> с операцией <code>executeQuery</code>.

- <code>query</code>: простые <code>SELECT</code>, <code>INSERT</code>, <code>UPDATE</code> и <code>DELETE</code>. SQL с комментариями, строковыми литералами, динамическим SQL и конструкциями вне проверенного подмножества не поддерживается.
- Значения передаются через плейсхолдеры <code>$1</code>, <code>$2</code> и далее. <code>options.queryReplacement</code> задаёт список expressions через запятую в том же порядке; параметры передаются драйверу отдельно, без конкатенации с SQL.
- <code>options.queryBatching</code>: обязательное значение <code>independently</code> или <code>transaction</code>.
- <code>options.connectionTimeout</code>: положительный таймаут подключения в секундах, по умолчанию 30.
- <code>options.largeNumbersOutput</code>: поддерживается только <code>text</code>.

PostgreSQL executeQuery использует асинхронный AsyncConnection psycopg 3. Подключение настраивается через переменные POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DATABASE, POSTGRES_USER и POSTGRES_PASSWORD.

## Expressions и данные

Выражения преобразуются в Python во время генерации. В runtime сгенерированного проекта нет парсера expressions и <code>eval()</code>.

| Конструкция | Поддержка |
|---|---|
| <code>$json</code>, <code>$json.name</code>, вложенные поля | Текущий JSON item |
| <code>$binary</code> и свойства binary | Binary текущего item |
| <code>$input.first()</code>, <code>$input.all()</code> | Первый item или полный список текущего входа |
| <code>$('Node Name').first()</code>, <code>.all()</code>, <code>.item</code> | Результат именованного узла с проверкой связи items |
| <code>$execution.id</code> | ID текущего generated-запуска |
| <code>{{ ... }}</code>, <code>={{ ... }}</code> | Expressions и текстовые шаблоны с несколькими вставками |
| Литералы, массивы, объекты, <code>JSON.stringify</code> | Простые литералы и сериализация поддержанных аргументов |
| <code>&amp;&amp;</code>, <code>||</code>, <code>!</code>, сравнения | Переводятся в соответствующие логические и сравнительные выражения Python |
| <code>Number(...)</code>, строковая конкатенация | Простые поддержанные случаи; плюс используется для строковой конкатенации |

Это не полный JavaScript. Произвольные функции, сложная арифметика и неподдержанные конструкции не вычисляются. Диагностика включает имя узла, параметр и исходное выражение. Неоднозначные linked-item ссылки не подменяются первым попавшимся item.

## Не включено в поддержку

Для поддерживаемого типа узла неподдерживаемый параметр, режим или expression не выдаётся за готовую логику: генератор оставляет TODO-функцию, которая сообщает о незавершённой реализации. Сами узлы с неизвестным типом, неподдерживаемые дополнительные соединения и узлы без обязательной ссылки на credentials могут быть исключены из generated-проекта. Примеры поведения, которое нужно реализовать вручную: JavaScript Code, дополнительные AI Agent каналы и tools, неподдержанный SQL, дополнительные режимы Merge и файловые операции вне перечней выше. Причина пропуска или TODO записывается в отчёт; после partial-конвертации проверьте связи и входные данные соседних функций.
