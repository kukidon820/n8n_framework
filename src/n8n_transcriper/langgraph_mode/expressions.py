"""Expression parsing helpers used only by opt-in LangGraph AI conversion."""

from __future__ import annotations

from n8n_transcriper.expressions import Expression, _expression, compile_expression
from n8n_transcriper.reader import WorkflowError


_CLOSING = {")": "(", "]": "[", "}": "{"}


def _template_spans(value: str) -> list[tuple[int, int, str]]:
    """Find ``{{ ... }}`` blocks without mistaking object braces for delimiters."""
    spans: list[tuple[int, int, str]] = []
    position = 0
    while position < len(value):
        start = value.find("{{", position)
        if start < 0:
            break
        expression_start = start + 2
        index = expression_start
        stack: list[str] = []
        quote: str | None = None
        escaped = False
        while index < len(value):
            char = value[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in "'\"`":
                quote = char
            elif char in "([{":
                stack.append(char)
            elif char in ")]}" :
                if char == "}" and not stack and value.startswith("}}", index):
                    end = index + 2
                    spans.append((start, end, value[expression_start:index].strip()))
                    position = end
                    break
                if not stack or stack.pop() != _CLOSING[char]:
                    raise ValueError("несогласованные скобки в expression")
            index += 1
        else:
            raise ValueError("не закрыт шаблон {{ ... }}")
    return spans


def compile_langgraph_expression(
    value: str, *, node: str, parameter: str
) -> Expression:
    """Compile n8n expressions with nested object literals for LangGraph AI.

    The shared converter parser remains untouched, so default generation keeps
    its existing behavior. This adapter handles nested ``}}`` tokens while
    compiling AI request bodies such as Gemini contents and structured prompts.
    """
    try:
        text = value[1:] if value.startswith("=") else value
        spans = _template_spans(text)
        if not spans:
            return compile_expression(value, node=node, parameter=parameter)

        pieces: list[str] = []
        dependencies: set[str] = set()
        cursor = 0
        for start, end, source in spans:
            if "{{" in text[cursor:start] or "}}" in text[cursor:start]:
                raise ValueError("незакрытый или некорректный шаблон")
            pieces.append(repr(text[cursor:start]))
            expression = _expression(source)
            dependencies.update(expression.dependencies)
            if start == 0 and end == len(text):
                return expression
            pieces.append(f"str({expression.code})")
            cursor = end
        tail = text[cursor:]
        if "{{" in tail or "}}" in tail:
            raise ValueError("незакрытый или некорректный шаблон")
        pieces.append(repr(tail))
        return Expression(" + ".join(pieces), frozenset(dependencies))
    except (ValueError, KeyError) as exc:
        raise WorkflowError(
            f"Узел {node}, параметр {parameter}, выражение {value!r}: {exc}"
        ) from exc
