"""Генерация Python-функций для операций n8n с binary и файлами.

Поддерживаются Convert to File (текст), ZIP compression/decompression и
Extract from File (текст/base64). Сгенерированные функции передают байты в
стандартной структуре n8n item без промежуточной сериализации в base64.
"""

import keyword
from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_file_node(node: dict[str, Any], function_name: str) -> str:
    """Сгенерировать реализацию одной файловой операции workflow.

    Ветвление здесь выполняется только во время конвертации. В generated-модуле
    остаётся конкретная операция с уже встроенными именами полей и настройками.

    Args:
        node: Узел Convert to File, Compression или Extract from File.
        function_name: Имя функции, которая будет вызвана из main.py.

    Returns:
        Текст Python-модуля для выбранной операции.

    Raises:
        WorkflowError: если тип, формат или параметр операции не поддерживается.
    """
    name = node.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    p = node.get("parameters", {})
    kind = node.get("type")
    options = p.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: options должен быть объектом")

    def check(values: dict, allowed: set[str]) -> None:
        """Проверить ключи конкретного словаря настроек.

        Неизвестные ключи отклоняются, чтобы генератор не создавал функцию с
        поведением, отличающимся от незамеченной настройки n8n.
        """
        extra = set(values) - allowed
        if extra:
            raise WorkflowError(f"Узел {name}: неподдерживаемые параметры: {', '.join(sorted(extra))}")

    # toText переносит исходный текст в реальное binary-поле с указанной кодировкой.
    if kind == "n8n-nodes-base.convertToFile" and p.get("operation") == "toText":
        check(p, {"operation", "sourceProperty", "options", "binaryPropertyName"})
        check(options, {"addBOM", "encoding", "fileName"})
        field = p.get("sourceProperty")
        if not isinstance(field, str) or not field:
            raise WorkflowError(f"Узел {name}: требуется sourceProperty")
        encoding = options.get("encoding", "utf8")
        bom = options.get("addBOM", False)
        if bom and encoding.lower().replace("-", "") != "utf8":
            raise WorkflowError(f"Узел {name}: BOM поддерживается только для UTF-8")
        filename = options.get("fileName", "file.txt")
        output = p.get("binaryPropertyName", "data")
        bom_bytes = bytes.fromhex("efbbbf") if bom else b""
        return f'''def {function_name}(_items):
    """Закодировать JSON-текст в binary-файл каждого item.

    Сохраняет остальные поля item и добавляет имя файла, MIME-тип и байты.
    """
    result = []
    for item in _items:
        content = item["json"][{field!r}].encode({encoding!r})
        content = {bom_bytes!r} + content
        binary = dict(item.get("binary", {{}}))
        binary[{output!r}] = {{"data": content, "fileName": {filename!r}, "mimeType": "text/plain"}}
        result.append({{**item, "binary": binary}})
    return result
'''
    # Compression использует zipfile и сохраняет файлы как bytes в binary-полях.
    if kind == "n8n-nodes-base.compression":
        operation = p.get("operation", "decompress")
        if operation == "compress":
            check(p, {"operation", "fileName", "binaryPropertyOutput", "options"})
            check(options, set())
            filename = p.get("fileName", "archive.zip")
            output = p.get("binaryPropertyOutput", "data")
            return f'''from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


def {function_name}(_items):
    """Упаковать все binary-файлы item в один ZIP-архив.

    Внутренние имена берутся из fileName, архив добавляется рядом с исходными полями.
    """
    result = []
    for item in _items:
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for key, file in item.get("binary", {{}}).items():
                archive.writestr(file.get("fileName") or key, file["data"])
        binary = dict(item.get("binary", {{}}))
        binary[{output!r}] = {{"data": buffer.getvalue(), "fileName": {filename!r}, "mimeType": "application/zip"}}
        result.append({{**item, "binary": binary}})
    return result
'''
        if operation == "decompress":
            check(p, {"operation", "binaryPropertyName", "options"})
            check(options, set())
            source = p.get("binaryPropertyName", "data")
            return f'''from io import BytesIO
from zipfile import ZipFile


def {function_name}(_items):
    """Распаковать ZIP и создать binary-поле для каждого файла в архиве.

    Каталоги пропускаются; файлы получают имена ``file_0``, ``file_1`` и далее.
    """
    result = []
    for item in _items:
        binary = dict(item.get("binary", {{}}))
        with ZipFile(BytesIO(binary[{source!r}]["data"])) as archive:
            for index, info in enumerate(entry for entry in archive.infolist() if not entry.is_dir()):
                binary[f"file_{{index}}"] = {{"data": archive.read(info), "fileName": info.filename}}
        result.append({{**item, "binary": binary}})
    return result
'''
    # ExtractFromFile записывает результат операции в JSON, сохраняя binary только по настройке.
    if kind == "n8n-nodes-base.extractFromFile":
        op = p.get("operation")
        check(p, {"operation", "binaryPropertyName", "destinationKey", "options"})
        source = p.get("binaryPropertyName", "data")
        dest = p.get("destinationKey", "data")
        if op == "binaryToPropery":
            check(options, {"keepSource"})
            keep = options.get("keepSource", "none")
            if keep not in ("none", "json", "binary", "both"):
                raise WorkflowError(f"Узел {name}: неподдерживаемый keepSource {keep!r}")
            return f'''import base64


def {function_name}(_items):
    """Закодировать выбранные bytes в base64 и записать строку в JSON.

    keepSource определяет, переносится ли исходный JSON и binary в результат.
    """
    result = []
    for item in _items:
        value = base64.b64encode(item["binary"][{source!r}]["data"]).decode("ascii")
        data = dict(item.get("json", {{}})) if {keep in ("json", "both")!r} else {{}}
        data[{dest!r}] = value
        output = {{"json": data}}
        if {keep in ("binary", "both")!r}:
            output["binary"] = dict(item.get("binary", {{}}))
        result.append(output)
    return result
'''
        if op == "text":
            check(options, {"encoding", "stripBOM"})
            encoding = options.get("encoding", "utf8")
            strip = options.get("stripBOM", False)
            return f'''def {function_name}(_items):
    """Декодировать binary-поле указанной кодировкой и записать текст в JSON.

    Если stripBOM включён, начальный символ BOM удаляется после декодирования.
    """
    result = []
    for item in _items:
        text = item["binary"][{source!r}]["data"].decode({encoding!r})
        if {strip!r}:
            text = text.removeprefix("\\ufeff")
        data = dict(item.get("json", {{}}))
        data[{dest!r}] = text
        result.append({{**item, "json": data}})
    return result
'''
    raise WorkflowError(f"Узел {name}: неподдерживаемая файловая операция {p.get('operation')!r}")
