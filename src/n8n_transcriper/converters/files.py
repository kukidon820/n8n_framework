"""Генерация простых функций для файловых узлов n8n."""

import keyword
from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_file_node(node: dict[str, Any], function_name: str) -> str:
    """Создать самостоятельный модуль для поддерживаемой операции с файлами."""
    name = node.get("name", "<без имени>")
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    p = node.get("parameters", {})
    kind = node.get("type")
    options = p.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: options должен быть объектом")

    def check(values: dict, allowed: set[str]) -> None:
        """Отклонить настройки операции, семантика которых не реализована."""
        extra = set(values) - allowed
        if extra:
            raise WorkflowError(f"Узел {name}: неподдерживаемые параметры: {', '.join(sorted(extra))}")

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
    """Создать бинарное текстовое поле из заданного JSON-поля."""
    result = []
    for item in _items:
        content = item["json"][{field!r}].encode({encoding!r})
        content = {bom_bytes!r} + content
        binary = dict(item.get("binary", {{}}))
        binary[{output!r}] = {{"data": content, "fileName": {filename!r}, "mimeType": "text/plain"}}
        result.append({{**item, "binary": binary}})
    return result
'''
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
    """Сжать binary-поля каждого item в ZIP-архив."""
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
    """Извлечь файлы из ZIP и сохранить их байты в binary-полях."""
    result = []
    for item in _items:
        binary = dict(item.get("binary", {{}}))
        with ZipFile(BytesIO(binary[{source!r}]["data"])) as archive:
            for index, info in enumerate(entry for entry in archive.infolist() if not entry.is_dir()):
                binary[f"file_{{index}}"] = {{"data": archive.read(info), "fileName": info.filename}}
        result.append({{**item, "binary": binary}})
    return result
'''
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
    """Преобразовать бинарное содержимое в base64-текстовое поле."""
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
    """Декодировать бинарное поле в текст и добавить его в JSON."""
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
