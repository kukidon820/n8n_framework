"""Преобразование простых выражений n8n в исходный код Python."""

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
            return _selector(matches[0].group(1).strip())
        pieces = []
        dependencies: set[str] = set()
        position = 0
        for match in matches:
            pieces.append(repr(text[position:match.start()]))
            part = _selector(match.group(1).strip())
            dependencies.update(part.dependencies)
            pieces.append(f"str({part.code})")
            position = match.end()
        pieces.append(repr(text[position:]))
        return Expression(" + ".join(pieces), frozenset(dependencies))
    except ValueError as exc:
        raise WorkflowError(f"Узел {node}, параметр {parameter}, выражение {value!r}: {exc}") from exc
