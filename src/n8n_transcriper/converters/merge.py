"""Генерация узла Merge в режимах Append и Combine."""

from typing import Any

from n8n_transcriper.reader import WorkflowError


def generate_merge_node(node: dict[str, Any], function_name: str) -> tuple[str, int]:
    name = node["name"]
    p = node["parameters"]
    version = node.get("typeVersion")
    if not isinstance(version, (int, float)) or isinstance(version, bool) or version < 3 or version >= 4:
        raise WorkflowError(f"Узел {name}: поддерживается Merge версии 3")
    mode = p.get("mode", "append")
    if mode not in {"append", "combine"}:
        raise WorkflowError(f"Узел {name}: поддерживаются только Merge Append и Combine")
    count = p.get("numberInputs", 2)
    if not isinstance(count, int) or isinstance(count, bool) or not 2 <= count <= 10:
        raise WorkflowError(f"Узел {name}: некорректное число входов Merge")
    if mode == "append":
        if set(p) - {"mode", "numberInputs", "options"} or p.get("options", {}):
            raise WorkflowError(f"Узел {name}: неподдерживаемые настройки Merge Append")
        return f'''def {function_name}(inputs):
    result = []
    for items in inputs:
        result.extend(items)
    return result
''', count
    allowed = {"mode", "numberInputs", "combineBy", "options", "fieldsToMatchString", "advanced", "mergeByFields", "joinMode", "outputDataFrom"}
    if set(p) - allowed:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки Merge Combine")
    combine_by = p.get("combineBy", "combineByFields")
    if combine_by not in {"combineByPosition", "combineAll", "combineByFields"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемый вариант Merge Combine")
    if combine_by != "combineByFields" and set(p) & {"fieldsToMatchString", "advanced", "mergeByFields", "joinMode", "outputDataFrom"}:
        raise WorkflowError(f"Узел {name}: поля сопоставления не применимы к этому варианту Combine")
    if combine_by != "combineByPosition" and count != 2:
        raise WorkflowError(f"Узел {name}: для этого варианта Combine требуются два входа")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"clashHandling", "includeUnpaired", "multipleMatches", "disableDotNotation"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые опции Merge Combine")
    if combine_by != "combineByFields" and set(options) & {"multipleMatches", "disableDotNotation"}:
        raise WorkflowError(f"Узел {name}: опции сопоставления не применимы к этому варианту Combine")
    clash = options.get("clashHandling", {})
    if not isinstance(clash, dict) or set(clash) - {"values"} or not isinstance(clash.get("values", {}), dict):
        raise WorkflowError(f"Узел {name}: некорректная настройка конфликтов")
    clash = clash.get("values", {})
    if set(clash) - {"resolveClash", "mergeMode", "overrideEmpty"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемая настройка конфликтов")
    resolution = clash.get("resolveClash", "preferLast") or "preferLast"
    merge_mode = clash.get("mergeMode", "deepMerge")
    override_empty = clash.get("overrideEmpty", False)
    if resolution not in {"preferLast", "addSuffix", *(f"preferInput{i}" for i in range(1, count + 1))} or merge_mode not in {"deepMerge", "shallowMerge"} or not isinstance(override_empty, bool):
        raise WorkflowError(f"Узел {name}: неподдерживаемый способ разрешения конфликтов")
    include_unpaired = options.get("includeUnpaired", False)
    if not isinstance(include_unpaired, bool) or (include_unpaired and combine_by != "combineByPosition"):
        raise WorkflowError(f"Узел {name}: некорректный includeUnpaired")
    if combine_by == "combineByFields":
        join_mode = p.get("joinMode", "keepMatches")
        output_from = p.get("outputDataFrom", "both")
        if join_mode not in {"keepMatches", "keepNonMatches", "keepEverything", "enrichInput1", "enrichInput2"} or output_from not in {"both", "input1", "input2"}:
            raise WorkflowError(f"Узел {name}: неподдерживаемый вариант результата Combine")
        if join_mode not in {"keepMatches", "keepNonMatches"} and output_from != "both":
            raise WorkflowError(f"Узел {name}: outputDataFrom неприменим к выбранному joinMode")
        if options.get("multipleMatches", "all") not in {"all", "first"} or options.get("disableDotNotation", False) not in (True, False):
            raise WorkflowError(f"Узел {name}: неподдерживаемые настройки сопоставления")
        if p.get("advanced", False):
            group = p.get("mergeByFields", {})
            if not isinstance(group, dict) or set(group) != {"values"} or not isinstance(group["values"], list):
                raise WorkflowError(f"Узел {name}: некорректные поля сопоставления")
            fields = [(entry.get("field1"), entry.get("field2")) for entry in group["values"] if isinstance(entry, dict) and set(entry) == {"field1", "field2"}]
            if len(fields) != len(group["values"]):
                raise WorkflowError(f"Узел {name}: некорректные пары полей")
        else:
            value = p.get("fieldsToMatchString", "")
            if not isinstance(value, str):
                raise WorkflowError(f"Узел {name}: некорректные поля сопоставления")
            fields = [(field.strip(), field.strip()) for field in value.split(",")]
        if not fields or any(not isinstance(a, str) or not isinstance(b, str) or not a or not b or "[" in a + b or any(not part for field in (a, b) for part in field.split(".")) for a, b in fields):
            raise WorkflowError(f"Узел {name}: некорректный путь поля сопоставления")
        if resolution == "preferLast" and "resolveClash" not in clash and join_mode == "enrichInput2":
            resolution = "preferInput1"
        pairs = f'''    if not inputs[0] or not inputs[1]:
        if {join_mode!r} == "keepMatches" or ({join_mode!r} == "enrichInput1" and not inputs[0]) or ({join_mode!r} == "enrichInput2" and not inputs[1]):
            return []
        if {join_mode!r} == "keepNonMatches" and (({output_from!r} == "input1" and not inputs[0]) or ({output_from!r} == "input2" and not inputs[1])):
            return []
        return deepcopy(inputs[0] + inputs[1])
    matched_pairs = []
    matched_left = set()
    matched_right = set()
    for left_index, first in enumerate(inputs[0]):
        for right_index, second in enumerate(inputs[1]):
            if all(match_field(first["json"], second["json"], a, b, {options.get('disableDotNotation', False)!r}) for a, b in {fields!r}):
                matched_pairs.append((left_index, right_index))
                matched_left.add(left_index)
                matched_right.add(right_index)
                if {options.get("multipleMatches", "all") == "first"!r}:
                    break
    if {join_mode!r} != "keepNonMatches":
        if {join_mode!r} == "keepMatches" and {output_from!r} == "input1":
            output.extend(deepcopy(item) for index, item in enumerate(inputs[0]) if index in matched_left)
        elif {join_mode!r} == "keepMatches" and {output_from!r} == "input2":
            output.extend(deepcopy(item) for index, item in enumerate(inputs[1]) if index in matched_right)
        else:
            output.extend(combine([inputs[0][left], inputs[1][right]]) for left, right in matched_pairs)
    if {join_mode!r} in ("keepNonMatches", "keepEverything", "enrichInput1"):
        for index, item in enumerate(inputs[0]):
            if index not in matched_left and ({join_mode!r} != "keepNonMatches" or {output_from!r} in ("both", "input1")):
                entry = deepcopy(item)
                if {join_mode!r} == "keepNonMatches" and {output_from!r} == "both":
                    entry["json"]["_source"] = "input1"
                elif {resolution == 'addSuffix'!r} and {join_mode!r} != "keepNonMatches":
                    entry["json"] = {{f"{{key}}_1": value for key, value in entry["json"].items()}}
                output.append(entry)
    if {join_mode!r} in ("keepNonMatches", "keepEverything", "enrichInput2"):
        for index, item in enumerate(inputs[1]):
            if index not in matched_right and ({join_mode!r} != "keepNonMatches" or {output_from!r} in ("both", "input2")):
                entry = deepcopy(item)
                if {join_mode!r} == "keepNonMatches" and {output_from!r} == "both":
                    entry["json"]["_source"] = "input2"
                elif {resolution == 'addSuffix'!r} and {join_mode!r} != "keepNonMatches":
                    entry["json"] = {{f"{{key}}_2": value for key, value in entry["json"].items()}}
                output.append(entry)
'''
    elif combine_by == "combineAll":
        pairs = '''    for first in inputs[0]:
        for second in inputs[1]:
            output.append(combine([first, second]))
'''
    else:
        pairs = f'''    count = {"max" if include_unpaired else "min"}(len(items) for items in inputs)
    for index in range(count):
        output.append(combine([items[index] if index < len(items) else {{"json": {{}}}} for items in inputs]))
'''
    preferred = int(resolution[len("preferInput"):]) - 1 if resolution.startswith("preferInput") else count - 1
    source = f'''from copy import deepcopy


def same_value(left, right):
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_value(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right))
    return left == right


def field_value(data, name, literal):
    current = data
    for key in [name] if literal else name.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return False, None
    return True, current


def match_field(left, right, first, second, literal):
    left_exists, left_value = field_value(left, first, literal)
    right_exists, right_value = field_value(right, second, literal)
    return left_exists and right_exists and same_value(left_value, right_value)


def merge_values(target, source, deep=True, skip_empty=False):
    if deep and isinstance(target, dict) and isinstance(source, dict):
        result = deepcopy(target)
        for key, value in source.items():
            if skip_empty and (value is None or value == "") and key in result:
                continue
            result[key] = merge_values(result[key], value, True, skip_empty) if key in result else deepcopy(value)
        return result
    if deep and isinstance(target, list) and isinstance(source, list):
        result = deepcopy(target)
        for index, value in enumerate(source):
            if index < len(result):
                if not (skip_empty and (value is None or value == "")):
                    result[index] = merge_values(result[index], value, True, skip_empty)
            else:
                result.append(deepcopy(value))
        return result
    return deepcopy(source)


def combine(entries):
    data, binary = {{}}, {{}}
    preferred = {preferred}
    order = [index for index in range(len(entries)) if index != preferred] + [preferred]
    for index in order:
        entry = entries[index]
        incoming = entry.get("json", {{}})
        if {resolution == 'addSuffix'!r}:
            incoming = {{f"{{key}}_{{index + 1}}": value for key, value in incoming.items()}}
        if {merge_mode == 'shallowMerge'!r}:
            for key, value in incoming.items():
                if not ({override_empty!r} and key in data and (value is None or value == "")):
                    data[key] = deepcopy(value)
        else:
            data = merge_values(data, incoming, True, {override_empty!r})
        binary = merge_values(binary, entry.get("binary", {{}}))
    return {{"json": data, "binary": binary}}


def {function_name}(inputs):
    output = []
{pairs}    return output
'''
    return source, count
