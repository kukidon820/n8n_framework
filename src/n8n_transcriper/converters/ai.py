"""Генерация вызова DeepSeek для агента без инструментов."""

import keyword
from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


def generate_deepseek_agent(agent: dict[str, Any], model: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Создать обычный HTTP-вызов агента с подключённой моделью DeepSeek."""
    name = agent.get("name", "<без имени>")
    p = agent.get("parameters", {})
    mp = model.get("parameters", {})
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise WorkflowError(f"Узел {name}: некорректное имя функции")
    if model.get("type") != "@n8n/n8n-nodes-langchain.lmChatDeepSeek":
        raise WorkflowError(f"Узел {name}: поддерживается только модель DeepSeek")
    if set(p) - {"promptType", "text", "options"} or p.get("promptType") != "define":
        raise WorkflowError(f"Узел {name}: неподдерживаемые параметры агента")
    options = p.get("options", {})
    if not isinstance(options, dict) or set(options) - {"systemMessage", "maxIterations", "enableStreaming"} or options.get("enableStreaming", False):
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки агента")
    if not isinstance(p.get("text"), str) or not isinstance(options.get("systemMessage"), str):
        raise WorkflowError(f"Узел {name}: требуется текст prompt и systemMessage")
    if set(mp) - {"model", "options"} or not isinstance(mp.get("model"), str) or not mp["model"]:
        raise WorkflowError(f"Узел {name}: требуется поддерживаемая конфигурация модели")
    mo = mp.get("options", {})
    if not isinstance(mo, dict) or set(mo) - {"temperature", "maxTokens", "timeout", "maxRetries"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемые настройки модели")
    if "timeout" not in mo or "maxRetries" not in mo:
        raise WorkflowError(f"Узел {name}: необходимо явно задать timeout и maxRetries модели")
    timeout, retries = mo["timeout"], mo["maxRetries"]
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
        raise WorkflowError(f"Узел {name}: некорректный timeout или maxRetries")
    payload = {"model": mp["model"], "stream": False}
    for n8n_key, api_key in (("temperature", "temperature"), ("maxTokens", "max_tokens")):
        if n8n_key in mo:
            value = mo[n8n_key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise WorkflowError(f"Узел {name}: некорректный {n8n_key}")
            payload[api_key] = value
    prompt = compile_expression(p["text"], node=name, parameter="text")
    system = compile_expression(options["systemMessage"], node=name, parameter="options.systemMessage")
    source = f'''import json
import time

import httpx
import config


def {function_name}(items, results, execution):
    """Вызвать DeepSeek и вернуть ответы модели как n8n items."""
    if not config.DEEPSEEK_API_KEY:
        raise ValueError("Не задан DEEPSEEK_API_KEY")
    output = []
    for item in items:
        payload = {payload!r}
        payload["messages"] = [
            {{"role": "system", "content": {system.code}}},
            {{"role": "user", "content": {prompt.code}}},
        ]
        for attempt in range({retries + 1}):
            try:
                response = httpx.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={{"Authorization": f"Bearer {{config.DEEPSEEK_API_KEY}}"}},
                    json=payload, timeout={timeout / 1000!r},
                )
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == {retries}:
                    raise
                time.sleep(min(2 ** attempt, 8))
        output.append({{"json": {{"output": response.json()["choices"][0]["message"]["content"]}}}})
    return output
'''
    return source, set(prompt.dependencies | system.dependencies)
