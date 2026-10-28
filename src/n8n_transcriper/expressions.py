"""Преобразование простых выражений n8n в исходный код Python."""

import ast
import re
from dataclasses import dataclass

from n8n_transcriper.reader import WorkflowError


@dataclass(frozen=True)
class Expression:
    code: str
    dependencies: frozenset[str]


_ROOT = re.compile(r"\$(json|binary|execution|input)|\$\((['\"])(.*?)\2\)")
_PATH = re.compile(r"\.([A-Za-z_$][\w$]*)|\[(\d+)\]|\[(['\"])(.*?)\3\]")
_TEMPLATE = re.compile(r"{{\s*(.*?)\s*}}", re.DOTALL)


def _split_top_level(value: str, delimiter: str) -> list[str]:
    parts = []
    start = 0
    stack = []
    quote = None
    escaped = False
    pairs = {')': '(', ']': '[', '}': '{'}
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            stack.append(char)
        elif char in ")]}:":
            if char == ":":
                if delimiter == ":" and not stack:
                    parts.append(value[start:index])
                    start = index + 1
            elif not stack or stack.pop() != pairs[char]:
                raise ValueError("несогласованные скобки")
        elif char == delimiter and not stack:
            parts.append(value[start:index])
            start = index + 1
    if quote or stack:
        raise ValueError("незакрытая строка или скобка")
    parts.append(value[start:])
    return parts


def _expression(value: str) -> Expression:
    value = value.strip()
    parts = _split_top_level(value, "+")
    if len(parts) > 1:
        compiled = [_expression(part) for part in parts]
        if not any(part.strip().startswith(("'", '"', "JSON.stringify(")) for part in parts):
            raise ValueError("сложение без строкового операнда не поддерживается")
        return Expression("''.join([" + ", ".join(part.code for part in compiled) + "])",
                          frozenset().union(*(part.dependencies for part in compiled)))
    if value.startswith("JSON.stringify(") and value.endswith(")"):
        inner = _expression(value[len("JSON.stringify("):-1])
        return Expression(f"json.dumps({inner.code}, ensure_ascii=False, separators=(',', ':'))", inner.dependencies)
    if value.startswith("{") and value.endswith("}"):
        entries = []
        dependencies: set[str] = set()
        inside = value[1:-1].strip()
        for field in _split_top_level(inside, ",") if inside else []:
            pair = _split_top_level(field, ":")
            if len(pair) != 2 or not re.fullmatch(r"[A-Za-z_$][\w$]*", pair[0].strip()):
                raise ValueError("неподдерживаемое поле объекта")
            item = _expression(pair[1])
            entries.append(f"{pair[0].strip()!r}: {item.code}")
            dependencies.update(item.dependencies)
        return Expression("{" + ", ".join(entries) + "}", frozenset(dependencies))
    if value.startswith(("'", '"')):
        try:
            literal = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("неподдерживаемая строковая константа") from exc
        if not isinstance(literal, str):
            raise ValueError("ожидалась строковая константа")
        return Expression(repr(literal), frozenset())
    return _selector(value)


def _selector(value: str) -> Expression:
    match = _ROOT.match(value)
    if not match:
        raise ValueError("поддерживаются только ссылки на данные n8n")
    root, _, node = match.groups()
    rest = value[match.end():]
    dependencies = frozenset([node]) if node is not None else frozenset()
    if node is not None:
        base = f"results[{node!r}]"
    elif root == "input":
        base = "items"
    elif root == "execution":
        base = "execution"
    else:
        base = f"item[{root!r}]"
    if node is not None or root == "input":
        if rest.startswith(".first()"):
            base += "[0]"
            rest = rest[len(".first()"):]
        elif rest.startswith(".all()"):
            rest = rest[len(".all()"):]
        else:
            raise ValueError("для набора items требуется first() или all()")
    while rest:
        part = _PATH.match(rest)
        if not part:
            raise ValueError(f"неподдерживаемый фрагмент {rest!r}")
        key = part.group(1) or part.group(4)
        if part.group(2) is not None:
            base += f"[{int(part.group(2))}]"
        else:
            base += f"[{key!r}]"
        rest = rest[part.end():]
    return Expression(base, dependencies)


def compile_expression(value: str, *, node: str, parameter: str) -> Expression:
    """Вернуть Python-код и зависимости или понятную ошибку преобразования."""
    try:
        text = value[1:] if value.startswith("=") else value
        matches = list(_TEMPLATE.finditer(text))
        remainder = _TEMPLATE.sub("", text)
        if "{{" in remainder or "}}" in remainder:
            raise ValueError("незакрытый или некорректный шаблон")
        if not matches:
            if value.startswith("="):
                raise ValueError("не найдено выражение в {{ }}")
            return Expression(repr(value), frozenset())
        if len(matches) == 1 and matches[0].span() == (0, len(text)):
            return _expression(matches[0].group(1).strip())
        pieces = []
        dependencies: set[str] = set()
        position = 0
        for match in matches:
            pieces.append(repr(text[position:match.start()]))
            part = _expression(match.group(1).strip())
            dependencies.update(part.dependencies)
            pieces.append(f"str({part.code})")
            position = match.end()
        pieces.append(repr(text[position:]))
        return Expression(" + ".join(pieces), frozenset(dependencies))
    except ValueError as exc:
        raise WorkflowError(f"Узел {node}, параметр {parameter}, выражение {value!r}: {exc}") from exc
