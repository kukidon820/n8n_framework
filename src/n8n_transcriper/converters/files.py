"""Генерация Python-функций для операций n8n с binary и файлами.

Поддерживаются все операции Convert to File из n8n, ZIP compression/decompression и
Extract from File (текст/base64). Сгенерированные функции передают байты в
стандартной структуре n8n item без промежуточной сериализации в base64.
"""

import codecs
import keyword
import json
import textwrap
from typing import Any

from n8n_transcriper.reader import WorkflowError


CONVERT_TO_FILE_OPERATIONS = {
    "csv",
    "html",
    "iCal",
    "toJson",
    "ods",
    "rtf",
    "toText",
    "xls",
    "xlsx",
    "toBinary",
}


_SPREADSHEET_HELPERS = textwrap.dedent(
    '''\
    import csv
    import html
    import io
    import json
    import zipfile
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import escape, quoteattr

    def _flatten_json(value, prefix=""):
        if not isinstance(value, dict):
            return {prefix: value} if prefix else {}
        result = {}
        for key, nested in value.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(nested, dict) and nested:
                result.update(_flatten_json(nested, field))
            else:
                result[field] = nested
        return result

    def _cell_value(value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value

    def _cell_text(value):
        value = _cell_value(value)
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _column_name(number):
        name = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        return name

    def _rtf_escape(value):
        escaped = []
        for character in _cell_text(value):
            codepoint = ord(character)
            if character in (chr(92), "{", "}"):
                escaped.append(chr(92) + character)
            elif codepoint == 10:
                escaped.append(chr(92) + "line ")
            elif codepoint < 32 or codepoint > 126:
                units = character.encode("utf-16-le", errors="surrogatepass")
                for offset in range(0, len(units), 2):
                    unit = int.from_bytes(units[offset:offset + 2], "little")
                    signed = unit if unit < 32768 else unit - 65536
                    escaped.append(chr(92) + f"u{signed}?")
            else:
                escaped.append(character)
        return "".join(escaped)

    def _ods_bytes(matrix, sheet_name, compressed):
        office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        table_ns = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
        text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        manifest_ns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
        ET.register_namespace("office", office)
        ET.register_namespace("table", table_ns)
        ET.register_namespace("text", text_ns)
        ET.register_namespace("manifest", manifest_ns)
        tag = lambda namespace, name: "{" + namespace + "}" + name
        root = ET.Element(tag(office, "document-content"), {tag(office, "version"): "1.3"})
        body = ET.SubElement(root, tag(office, "body"))
        spreadsheet = ET.SubElement(body, tag(office, "spreadsheet"))
        table = ET.SubElement(spreadsheet, tag(table_ns, "table"), {tag(table_ns, "name"): sheet_name})
        for values in matrix:
            row = ET.SubElement(table, tag(table_ns, "table-row"))
            for value in values:
                cell = ET.SubElement(row, tag(table_ns, "table-cell"))
                if isinstance(value, bool):
                    cell.set(tag(office, "value-type"), "boolean")
                    cell.set(tag(office, "boolean-value"), "true" if value else "false")
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    cell.set(tag(office, "value-type"), "float")
                    cell.set(tag(office, "value"), str(value))
                else:
                    cell.set(tag(office, "value-type"), "string")
                paragraph = ET.SubElement(cell, tag(text_ns, "p"))
                paragraph.text = _cell_text(value)
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        manifest = ET.Element(tag(manifest_ns, "manifest"), {tag(manifest_ns, "version"): "1.3"})
        ET.SubElement(manifest, tag(manifest_ns, "file-entry"), {
            tag(manifest_ns, "full-path"): "/",
            tag(manifest_ns, "media-type"): "application/vnd.oasis.opendocument.spreadsheet",
            tag(manifest_ns, "version"): "1.3",
        })
        ET.SubElement(manifest, tag(manifest_ns, "file-entry"), {
            tag(manifest_ns, "full-path"): "content.xml",
            tag(manifest_ns, "media-type"): "text/xml",
        })
        manifest_bytes = ET.tostring(manifest, encoding="utf-8", xml_declaration=True)
        buffer = io.BytesIO()
        method = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
        with zipfile.ZipFile(buffer, "w", method) as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet", compress_type=zipfile.ZIP_STORED)
            archive.writestr("content.xml", content)
            archive.writestr("META-INF/manifest.xml", manifest_bytes)
        return buffer.getvalue()

    def _xlsx_bytes(matrix, sheet_name, compressed):
        rows = []
        for row_number, values in enumerate(matrix, start=1):
            cells = []
            for column_number, value in enumerate(values, start=1):
                if value is None:
                    continue
                address = f"{_column_name(column_number)}{row_number}"
                if isinstance(value, bool):
                    cells.append(f'<c r="{address}" t="b"><v>{1 if value else 0}</v></c>')
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    cells.append(f'<c r="{address}"><v>{value}</v></c>')
                else:
                    text = escape(_cell_text(value))
                    cells.append(f'<c r="{address}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
            rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                     f'<sheetData>{"".join(rows)}</sheetData></worksheet>')
        workbook_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                        f'<sheets><sheet name={quoteattr(sheet_name)} sheetId="1" r:id="rId1"/></sheets></workbook>')
        workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                         '</Relationships>')
        root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                     '</Relationships>')
        content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                         '</Types>')
        buffer = io.BytesIO()
        method = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
        with zipfile.ZipFile(buffer, "w", method) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        return buffer.getvalue()

    def _spreadsheet_bytes(operation, matrix, delimiter, sheet_name, compressed, header_row):
        if operation == "csv":
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, delimiter=delimiter, lineterminator="\\n")
            writer.writerows([[_cell_value(value) for value in row] for row in matrix])
            return stream.getvalue().encode("utf-8")
        if operation == "html":
            table = ["<!doctype html><html><head><meta charset=\\"utf-8\\"></head><body><table>"]
            for row_index, row in enumerate(matrix):
                tag = "th" if header_row and row_index == 0 else "td"
                cells = "".join(f"<{tag}>{html.escape(_cell_text(value))}</{tag}>" for value in row)
                table.append(f"<tr>{cells}</tr>")
            table.append("</table></body></html>")
            return "\\n".join(table).encode("utf-8")
        if operation == "rtf":
            rows = []
            width = 2000
            for row in matrix:
                boundaries = "".join(f"\\\\cellx{width * index}" for index in range(1, len(row) + 1))
                cells = "".join(_rtf_escape(value) + "\\\\cell " for value in row)
                rows.append("\\\\trowd\\\\trgaph108 " + boundaries + " " + cells + "\\\\row")
            return ("{\\\\rtf1\\\\ansi " + "\\\\n".join(rows) + "}").encode("utf-8")
        if operation == "ods":
            return _ods_bytes(matrix, sheet_name, compressed)
        if operation == "xlsx":
            return _xlsx_bytes(matrix, sheet_name, compressed)
        if operation == "xls":
            import xlwt
            workbook = xlwt.Workbook()
            sheet = workbook.add_sheet(sheet_name[:31] or "Sheet")
            for row_number, row in enumerate(matrix):
                for column_number, value in enumerate(row):
                    if value is not None:
                        sheet.write(row_number, column_number, _cell_value(value))
            stream = io.BytesIO()
            workbook.save(stream)
            return stream.getvalue()
        raise ValueError(f"Unsupported spreadsheet operation: {operation}")
    '''
)


_BINARY_HELPERS = textwrap.dedent(
    '''\
    import base64
    import codecs
    import mimetypes
    import re

    def _get_path(value, path):
        for part in path.split("."):
            if isinstance(value, dict):
                value = value[part]
            elif isinstance(value, list):
                value = value[int(part)]
            else:
                raise KeyError(path)
        return value

    def _encoding_name(value):
        aliases = {
            "utf8": "utf-8", "utf16": "utf-16-le", "utf16le": "utf-16-le", "utf16be": "utf-16-be",
            "ucs2": "utf-16-le", "latin1": "latin-1", "binary": "latin-1",
        }
        name = aliases.get(value.lower().replace("-", ""), value)
        return codecs.lookup(name).name

    def _encode_text(value, encoding, add_bom):
        name = _encoding_name(encoding)
        if name == "utf-8":
            result = value.encode("utf-8")
            return b"\\xef\\xbb\\xbf" + result if add_bom else result
        if name == "utf-16-le":
            result = value.encode("utf-16-le")
            return b"\\xff\\xfe" + result if add_bom else result
        if name == "utf-16-be":
            result = value.encode("utf-16-be")
            return b"\\xfe\\xff" + result if add_bom else result
        return value.encode(name)

    def _binary(data, filename, mime_type):
        return {"data": data, "fileName": filename, "mimeType": mime_type}
    '''
)


def file_node_dependencies(node: dict[str, Any]) -> set[str]:
    """Вернуть сторонние библиотеки, нужные конкретной файловой операции."""
    parameters = node.get("parameters", {})
    operation = parameters.get("operation", "csv")
    if node.get("type") != "n8n-nodes-base.convertToFile":
        return set()
    dependencies = {"xlwt>=1.3.0"} if operation == "xls" else set()
    additional_fields = parameters.get("additionalFields", {})
    if (
        operation == "iCal"
        and isinstance(additional_fields, dict)
        and additional_fields.get("useWorkflowTimezone")
    ):
        dependencies.add("tzdata>=2024.2; platform_system == 'Windows'")
    return dependencies


def _generate_spreadsheet(
    node: dict[str, Any], function_name: str, operation: str
) -> str:
    """Сгенерировать самостоятельный модуль для табличного формата."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    options = parameters.get("options", {})
    allowed_options = {"fileName", "headerRow"}
    if operation == "csv":
        allowed_options.add("delimiter")
    if operation in {"ods", "xls", "xlsx"}:
        allowed_options.add("sheetName")
    if operation in {"ods", "xlsx"}:
        allowed_options.add("compression")
    unknown = set(options) - allowed_options
    if unknown:
        raise WorkflowError(
            f"Узел {name}: для Convert to File ({operation}) не поддерживаются параметры: "
            f"{', '.join(sorted(unknown))}"
        )
    output = parameters.get("binaryPropertyName", "data")
    filename = options.get("fileName") or f"File.{operation}"
    header_row = options.get("headerRow", True)
    delimiter = options.get("delimiter", ",")
    sheet_name = options.get("sheetName", "Sheet")
    compression = options.get("compression", False)
    if not isinstance(output, str) or not output:
        raise WorkflowError(f"Узел {name}: binaryPropertyName должен быть непустой строкой")
    if not isinstance(filename, str):
        raise WorkflowError(f"Узел {name}: options.fileName должен быть строкой")
    if not isinstance(header_row, bool):
        raise WorkflowError(f"Узел {name}: options.headerRow должен быть логическим значением")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise WorkflowError(f"Узел {name}: options.delimiter должен содержать один символ")
    if not isinstance(sheet_name, str) or not sheet_name:
        raise WorkflowError(f"Узел {name}: options.sheetName должен быть непустой строкой")
    if not isinstance(compression, bool):
        raise WorkflowError(f"Узел {name}: options.compression должен быть логическим значением")

    mime_types = {
        "csv": "text/csv",
        "html": "text/html",
        "rtf": "application/rtf",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    generated_function = textwrap.dedent(
        '''\
        def __FUNCTION__(_items):
            # Свернуть поля всех входных items в одну таблицу, как делает n8n.
            rows = [_flatten_json(item.get("json", {}) or {}) for item in _items]
            columns = list(dict.fromkeys(key for row in rows for key in row))
            matrix = [columns] if __HEADER_ROW__ else []
            matrix.extend([[row.get(column) for column in columns] for row in rows])
            content = _spreadsheet_bytes(
                __OPERATION__, matrix, __DELIMITER__, __SHEET_NAME__, __COMPRESSION__, __HEADER_ROW__
            )
            binary = {
                __OUTPUT_FIELD__: {
                    "data": content,
                    "fileName": __FILE_NAME__,
                    "mimeType": __MIME_TYPE__,
                }
            }
            return [{"json": {}, "binary": binary}]
        '''
    )
    replacements = {
        "__FUNCTION__": function_name,
        "__HEADER_ROW__": repr(header_row),
        "__OPERATION__": repr(operation),
        "__DELIMITER__": repr(delimiter),
        "__SHEET_NAME__": repr(sheet_name),
        "__COMPRESSION__": repr(compression),
        "__OUTPUT_FIELD__": repr(output),
        "__FILE_NAME__": repr(filename),
        "__MIME_TYPE__": repr(mime_types[operation]),
    }
    for marker, value in replacements.items():
        generated_function = generated_function.replace(marker, value)
    return _SPREADSHEET_HELPERS + "\n\n" + generated_function


def _render_template(template: str, replacements: dict[str, str]) -> str:
    """Вставить проверенные Python-литералы в шаблон generated-модуля."""
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _check_encoding(name: str, encoding: Any) -> str:
    """Проверить имя кодировки на этапе генерации, а не при запуске проекта."""
    if not isinstance(encoding, str) or not encoding:
        raise WorkflowError(f"Узел {name}: encoding должен быть непустой строкой")
    if encoding.lower().replace("-", "") == "cesu8":
        raise WorkflowError(f"Узел {name}: кодировка CESU-8 не поддерживается Python")
    try:
        codecs.lookup(encoding.lower().replace("ucs2", "utf-16-le"))
    except LookupError as exc:
        raise WorkflowError(f"Узел {name}: неизвестная кодировка {encoding!r}") from exc
    return encoding


def _check_bom(name: str, encoding: str, add_bom: Any) -> bool:
    """Проверить флаг BOM и убедиться, что выбранная кодировка его поддерживает."""
    if not isinstance(add_bom, bool):
        raise WorkflowError(f"Узел {name}: options.addBOM должен быть логическим значением")
    normalized = encoding.lower().replace("-", "")
    if add_bom and normalized not in {"utf8", "ucs2", "utf16", "utf16le", "utf16be"}:
        raise WorkflowError(f"Узел {name}: BOM поддерживается только для UTF-8 и UTF-16")
    return add_bom


def _generate_text_file(node: dict[str, Any], function_name: str, check: Any) -> str:
    """Создать generated-функцию, которая пишет строковое поле в binary item."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    options = parameters.get("options", {})
    check(parameters, {"operation", "sourceProperty", "options", "binaryPropertyName"})
    check(options, {"addBOM", "encoding", "fileName"})
    source = parameters.get("sourceProperty")
    output = parameters.get("binaryPropertyName", "data")
    encoding = _check_encoding(name, options.get("encoding", "utf8"))
    add_bom = _check_bom(name, encoding, options.get("addBOM", False))
    filename = options.get("fileName") or "file.txt"
    if not isinstance(source, str) or not source:
        raise WorkflowError(f"Узел {name}: для Convert to Text File требуется sourceProperty")
    if not isinstance(output, str) or not output:
        raise WorkflowError(f"Узел {name}: binaryPropertyName должен быть непустой строкой")
    if not isinstance(filename, str):
        raise WorkflowError(f"Узел {name}: options.fileName должен быть строкой")
    module = _BINARY_HELPERS + textwrap.dedent(
        '''\
        def __FUNCTION__(_items):
            result = []
            for item in _items:
                value = _get_path(item.get("json", {}), __SOURCE_FIELD__)
                mime_type = "text/plain"
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    mime_type = "application/json"
                content = _encode_text(str(value), __ENCODING__, __ADD_BOM__)
                result.append({
                    "json": {},
                    "binary": {__OUTPUT_FIELD__: _binary(content, __FILE_NAME__, mime_type)},
                })
            return result
        '''
    )
    return _render_template(
        module.replace("import base64", "import base64\nimport json", 1),
        {
            "__FUNCTION__": function_name,
            "__SOURCE_FIELD__": repr(source),
            "__ENCODING__": repr(encoding),
            "__ADD_BOM__": repr(add_bom),
            "__OUTPUT_FIELD__": repr(output),
            "__FILE_NAME__": repr(filename),
        },
    )


def _generate_json_file(node: dict[str, Any], function_name: str, check: Any) -> str:
    """Сгенерировать операцию toJson с режимом одного или нескольких файлов."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    options = parameters.get("options", {})
    check(parameters, {"operation", "mode", "options", "binaryPropertyName"})
    check(options, {"addBOM", "encoding", "fileName", "format"})
    mode = parameters.get("mode", "once")
    output = parameters.get("binaryPropertyName", "data")
    encoding = _check_encoding(name, options.get("encoding", "utf8"))
    add_bom = _check_bom(name, encoding, options.get("addBOM", False))
    pretty = options.get("format", False)
    filename = options.get("fileName") or "file.json"
    if not isinstance(mode, str) or mode not in {"once", "each"}:
        raise WorkflowError(f"Узел {name}: режим toJson должен быть once или each")
    if not isinstance(output, str) or not output:
        raise WorkflowError(f"Узел {name}: binaryPropertyName должен быть непустой строкой")
    if not isinstance(pretty, bool):
        raise WorkflowError(f"Узел {name}: options.format должен быть логическим значением")
    if not isinstance(filename, str):
        raise WorkflowError(f"Узел {name}: options.fileName должен быть строкой")
    module = _BINARY_HELPERS + textwrap.dedent(
        '''\
        def __FUNCTION__(_items):
            result = []
            source_items = [_items] if __MODE__ == "once" else [[item] for item in _items]
            for group in source_items:
                values = [item.get("json", {}) for item in group]
                payload = values if __MODE__ == "once" else values[0]
                if __PRETTY__:
                    text = json.dumps(payload, ensure_ascii=False, indent=2)
                else:
                    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                content = _encode_text(text, __ENCODING__, __ADD_BOM__)
                result.append({
                    "json": {},
                    "binary": {__OUTPUT_FIELD__: _binary(content, __FILE_NAME__, "application/json")},
                })
            return result
        '''
    )
    module = module.replace("import base64", "import base64\nimport json", 1)
    return _render_template(
        module,
        {
            "__FUNCTION__": function_name,
            "__MODE__": repr(mode),
            "__PRETTY__": repr(pretty),
            "__ENCODING__": repr(encoding),
            "__ADD_BOM__": repr(add_bom),
            "__OUTPUT_FIELD__": repr(output),
            "__FILE_NAME__": repr(filename),
        },
    )


def _generate_binary_file(node: dict[str, Any], function_name: str, check: Any) -> str:
    """Сгенерировать операцию toBinary из base64 или текстового поля JSON."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    options = parameters.get("options", {})
    check(parameters, {"operation", "sourceProperty", "options", "binaryPropertyName"})
    check(options, {"addBOM", "dataIsBase64", "encoding", "fileName", "mimeType"})
    source = parameters.get("sourceProperty")
    output = parameters.get("binaryPropertyName", "data")
    node_version = node.get("typeVersion", 1)
    data_is_base64 = options.get("dataIsBase64", True) if node_version == 1 else True
    add_bom = options.get("addBOM", False)
    encoding = _check_encoding(name, options.get("encoding", "utf8"))
    filename = options.get("fileName", "")
    mime_type = options.get("mimeType", "")
    if not isinstance(source, str) or not source:
        raise WorkflowError(f"Узел {name}: для Move Base64 String to File требуется sourceProperty")
    if not isinstance(output, str) or not output:
        raise WorkflowError(f"Узел {name}: binaryPropertyName должен быть непустой строкой")
    if not isinstance(data_is_base64, bool) or not isinstance(add_bom, bool):
        raise WorkflowError(f"Узел {name}: dataIsBase64 и addBOM должны быть логическими значениями")
    if not data_is_base64:
        add_bom = _check_bom(name, encoding, add_bom)
    if not isinstance(filename, str) or not isinstance(mime_type, str):
        raise WorkflowError(f"Узел {name}: fileName и mimeType должны быть строками")
    if not isinstance(node_version, (int, float)) or isinstance(node_version, bool):
        node_version = 1
    strict_base64 = node_version >= 1.2
    module = _BINARY_HELPERS + textwrap.dedent(
        '''\
        import json

        def _decode_base64(value, strict):
            if isinstance(value, list):
                if strict and any(not isinstance(byte, int) or not 0 <= byte <= 255 for byte in value):
                    raise ValueError("ожидался массив байтов от 0 до 255")
                return bytes(value)
            if isinstance(value, dict) and value.get("type") == "Buffer" and isinstance(value.get("data"), list):
                return _decode_base64(value["data"], strict)
            if not isinstance(value, str):
                raise ValueError("значение должно быть строкой base64 или массивом байтов")
            encoded = value.strip()
            match = re.match(r"^data:([^;,]*)(?:;[^;,]*)*;base64,([\\s\\S]*)$", encoded, re.IGNORECASE)
            detected_mime = match.group(1) if match else ""
            if match:
                encoded = match.group(2)
            if strict:
                encoded = re.sub(r"\\s", "", encoded).replace("-", "+").replace("_", "/")
                if len(encoded) % 4 == 1:
                    raise ValueError("некорректная строка base64")
                encoded += "=" * (-len(encoded) % 4)
                try:
                    return base64.b64decode(encoded, validate=True), detected_mime
                except ValueError as error:
                    raise ValueError("некорректная строка base64") from error
            return base64.b64decode(encoded), detected_mime

        def __FUNCTION__(_items):
            result = []
            for item in _items:
                value = _get_path(item.get("json", {}), __SOURCE_FIELD__)
                mime_type = __MIME_TYPE__ or "application/octet-stream"
                if __DATA_IS_BASE64__:
                    decoded = _decode_base64(value, __STRICT_BASE64__)
                    if isinstance(decoded, tuple):
                        content, detected_mime = decoded
                        mime_type = __MIME_TYPE__ or detected_mime or mime_type
                    else:
                        content = decoded
                else:
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                        mime_type = "application/json"
                    content = _encode_text(str(value), __ENCODING__, __ADD_BOM__)
                filename = __FILE_NAME__
                if not filename:
                    filename = "file" + (mimetypes.guess_extension(mime_type) or "")
                result.append({
                    "json": {},
                    "binary": {__OUTPUT_FIELD__: _binary(content, filename, mime_type)},
                })
            return result
        '''
    )
    return _render_template(
        module,
        {
            "__FUNCTION__": function_name,
            "__SOURCE_FIELD__": repr(source),
            "__OUTPUT_FIELD__": repr(output),
            "__DATA_IS_BASE64__": repr(data_is_base64),
            "__STRICT_BASE64__": repr(strict_base64),
            "__ENCODING__": repr(encoding),
            "__ADD_BOM__": repr(add_bom),
            "__FILE_NAME__": repr(filename),
            "__MIME_TYPE__": repr(mime_type),
        },
    )


def _generate_ical_file(
    node: dict[str, Any], function_name: str, workflow_timezone: str | None, check: Any
) -> str:
    """Сгенерировать iCalendar-файл и сохранить поддерживаемые поля события."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    check(parameters, {"operation", "title", "start", "end", "allDay", "binaryPropertyName", "additionalFields"})
    options = parameters.get("additionalFields", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: additionalFields должен быть объектом")
    allowed = {
        "attendeesUi", "busyStatus", "calName", "description", "fileName", "geolocationUi",
        "location", "organizerUi", "recurrenceRule", "sequence", "status", "uid", "url",
        "useWorkflowTimezone",
    }
    extra = set(options) - allowed
    if extra:
        raise WorkflowError(f"Узел {name}: неподдерживаемые параметры iCal: {', '.join(sorted(extra))}")
    title = parameters.get("title", "")
    start = parameters.get("start", "")
    end = parameters.get("end") or start
    all_day = parameters.get("allDay", False)
    output = parameters.get("binaryPropertyName", "data")
    filename = options.get("fileName") or "event.ics"
    use_timezone = options.get("useWorkflowTimezone", False)
    if not isinstance(title, str) or not isinstance(start, str) or not isinstance(end, str):
        raise WorkflowError(f"Узел {name}: title, start и end должны быть строками")
    if not start:
        raise WorkflowError(f"Узел {name}: операции iCal требуется дата start")
    if not isinstance(all_day, bool) or not isinstance(use_timezone, bool):
        raise WorkflowError(f"Узел {name}: allDay и useWorkflowTimezone должны быть логическими значениями")
    if not isinstance(output, str) or not output or not isinstance(filename, str):
        raise WorkflowError(f"Узел {name}: binaryPropertyName и fileName должны быть строками")
    if use_timezone and not workflow_timezone:
        raise WorkflowError(f"Узел {name}: useWorkflowTimezone включён, но в настройках workflow нет timezone")
    event_options = dict(options)
    event_options.pop("fileName", None)
    event_options.pop("useWorkflowTimezone", None)
    event = {"title": title, "start": start, "end": end, "all_day": all_day, "options": event_options}
    module = textwrap.dedent(
        '''\
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        from zoneinfo import ZoneInfo

        def _escape_ical(value):
            return str(value).replace("\\\\", "\\\\\\\\").replace("\\n", "\\\\n").replace(",", "\\\\,").replace(";", "\\\\;")

        def _fold_ical(line):
            pieces = []
            current = ""
            for character in line:
                if len((current + character).encode("utf-8")) > 73:
                    pieces.append(current)
                    current = " " + character
                else:
                    current += character
            pieces.append(current)
            return "\\r\\n".join(pieces)

        def _parse_datetime(value, timezone_name):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timezone_name:
                zone = ZoneInfo(timezone_name)
                parsed = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
            elif parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        def __FUNCTION__(_items):
            event = __EVENT__
            result = []
            for _item in _items:
                options = event["options"]
                start = _parse_datetime(event["start"], __TIMEZONE__)
                end = _parse_datetime(event["end"], __TIMEZONE__)
                if event["all_day"]:
                    start_value = start.strftime("%Y%m%d")
                    end_value = (end.date() + timedelta(days=1)).strftime("%Y%m%d")
                    date_lines = [f"DTSTART;VALUE=DATE:{start_value}", f"DTEND;VALUE=DATE:{end_value}"]
                else:
                    date_lines = [f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}"]
                lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//n8n-transcriper//ConvertToFile//EN", "CALSCALE:GREGORIAN"]
                if options.get("calName"):
                    lines.append("X-WR-CALNAME:" + _escape_ical(options["calName"]))
                lines.extend(["BEGIN:VEVENT", "UID:" + _escape_ical(options.get("uid") or str(uuid4())), "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")])
                lines.extend(date_lines)
                lines.append("SUMMARY:" + _escape_ical(event["title"]))
                for key, property_name in (("description", "DESCRIPTION"), ("location", "LOCATION"), ("url", "URL")):
                    if options.get(key):
                        lines.append(property_name + ":" + _escape_ical(options[key]))
                status = options.get("status", "CONFIRMED")
                lines.append("STATUS:" + _escape_ical(status))
                if options.get("sequence") is not None:
                    lines.append("SEQUENCE:" + str(options.get("sequence", 0)))
                if options.get("recurrenceRule"):
                    rule = str(options["recurrenceRule"])
                    lines.append(rule if rule.startswith("RRULE:") else "RRULE:" + rule)
                if options.get("busyStatus"):
                    lines.append("X-MICROSOFT-CDO-BUSYSTATUS:" + _escape_ical(options["busyStatus"]))
                organizer = options.get("organizerUi", {}).get("organizerValues", {})
                if organizer.get("email"):
                    name = organizer.get("name", "")
                    prefix = "ORGANIZER;CN=" + _escape_ical(name) + ":mailto:" if name else "ORGANIZER:mailto:"
                    lines.append(prefix + _escape_ical(organizer["email"]))
                attendees = options.get("attendeesUi", {}).get("attendeeValues", [])
                if isinstance(attendees, dict):
                    attendees = [attendees]
                for attendee in attendees:
                    if attendee.get("email"):
                        name = attendee.get("name", "")
                        prefix = "ATTENDEE;CN=" + _escape_ical(name) if name else "ATTENDEE"
                        prefix += ";RSVP=" + ("TRUE" if attendee.get("rsvp") else "FALSE") + ":mailto:"
                        lines.append(prefix + _escape_ical(attendee["email"]))
                geo = options.get("geolocationUi", {}).get("geolocationValues", {})
                if geo.get("lat") not in (None, "") and geo.get("lon") not in (None, ""):
                    lines.append("GEO:" + str(geo["lat"]) + ";" + str(geo["lon"]))
                lines.extend(["END:VEVENT", "END:VCALENDAR"])
                content = ("\\r\\n".join(_fold_ical(line) for line in lines) + "\\r\\n").encode("utf-8")
                result.append({"json": {}, "binary": {__OUTPUT_FIELD__: {
                    "data": content, "fileName": __FILE_NAME__, "mimeType": "text/calendar"
                }}})
            return result
        '''
    )
    return _render_template(
        module,
        {
            "__FUNCTION__": function_name,
            "__EVENT__": repr(event),
            "__TIMEZONE__": repr(workflow_timezone if use_timezone else None),
            "__OUTPUT_FIELD__": repr(output),
            "__FILE_NAME__": repr(filename),
        },
    )


def generate_file_node(
    node: dict[str, Any], function_name: str, workflow_timezone: str | None = None
) -> str:
    """Сгенерировать реализацию одной файловой операции workflow.

    Ветвление здесь выполняется только во время конвертации. Для Convert to File
    учитывается n8n default operation ``csv``; в generated-модуле остаётся
    конкретная операция с уже встроенными именами полей и настройками.

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

    # n8n задаёт CSV как действие по умолчанию, если operation отсутствует.
    operation = p.get("operation", "csv") if kind == "n8n-nodes-base.convertToFile" else p.get("operation")

    if kind == "n8n-nodes-base.convertToFile":
        if not isinstance(operation, str):
            raise WorkflowError(f"Узел {name}: operation должна быть строковым названием действия Convert to File")
        if operation in {"csv", "html", "rtf", "ods", "xls", "xlsx"}:
            check(p, {"operation", "options", "binaryPropertyName"})
            return _generate_spreadsheet(node, function_name, operation)
        if operation == "toJson":
            return _generate_json_file(node, function_name, check)
        if operation == "toBinary":
            return _generate_binary_file(node, function_name, check)
        if operation == "iCal":
            return _generate_ical_file(node, function_name, workflow_timezone, check)
        if operation == "toText":
            return _generate_text_file(node, function_name, check)
        raise WorkflowError(
            f"Узел {name}: операция Convert to File {operation!r} неизвестна. "
            f"Поддерживаются: {', '.join(sorted(CONVERT_TO_FILE_OPERATIONS))}."
        )

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
