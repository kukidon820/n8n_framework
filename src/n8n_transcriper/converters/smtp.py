"""Преобразование Gmail API send в отправку письма через SMTP."""

from typing import Any

from n8n_transcriper.expressions import compile_expression
from n8n_transcriper.reader import WorkflowError


GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def is_gmail_send_node(node: dict[str, Any]) -> bool:
    """Определить HTTP Request, который отправляет письмо через Gmail API."""
    parameters = node.get("parameters", {})
    url = parameters.get("url") if isinstance(parameters, dict) else None
    return isinstance(url, str) and url.rstrip("/") == GMAIL_SEND_URL


def generate_smtp_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Создать SMTP-отправитель из MIME raw, ожидаемого Gmail API."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    if not isinstance(parameters, dict):
        raise WorkflowError(f"Узел {name}: параметры Gmail SMTP должны быть объектом")
    method = parameters.get("method", "GET")
    if not isinstance(method, str) or method.upper() != "POST":
        raise WorkflowError(f"Узел {name}: SMTP-конвертация Gmail требует метод POST")
    if not parameters.get("sendBody", False) or parameters.get("specifyBody") != "json":
        raise WorkflowError(f"Узел {name}: SMTP-конвертация Gmail требует JSON body с raw MIME")
    body_value = parameters.get("jsonBody")
    if not isinstance(body_value, str):
        raise WorkflowError(f"Узел {name}: SMTP-конвертация Gmail требует выражение jsonBody")
    options = parameters.get("options", {})
    timeout_ms = options.get("timeout", 60000) if isinstance(options, dict) else 60000
    if not isinstance(timeout_ms, (int, float)) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        raise WorkflowError(f"Узел {name}: SMTP timeout должен быть положительным числом")
    body = compile_expression(body_value, node=name, parameter="jsonBody")
    timeout = timeout_ms / 1000
    source = f'''import base64
import json
import smtplib
import ssl
from email.parser import BytesParser
from email.policy import SMTP
from email.utils import getaddresses

import config


def {function_name}(items, results, execution):
    """Отправить подготовленные MIME-письма через SMTP с паролем приложения."""
    if not config.SMTP_USERNAME or not config.SMTP_APP_PASSWORD:
        raise ValueError("Задайте SMTP_USERNAME и SMTP_APP_PASSWORD в config.py или окружении")
    if config.SMTP_USE_SSL and config.SMTP_STARTTLS:
        raise ValueError("Выберите SMTP_USE_SSL или SMTP_STARTTLS, но не оба режима")
    if not config.SMTP_USE_SSL and not config.SMTP_STARTTLS:
        raise ValueError("SMTP требует шифрование: включите SMTP_STARTTLS или SMTP_USE_SSL")

    sent = []
    for item in items:
        body = {body.code}
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict) or not isinstance(body.get("raw"), str) or not body["raw"]:
            raise ValueError("SMTP-узел ожидает Gmail raw MIME в jsonBody")

        raw = body["raw"]
        raw += "=" * (-len(raw) % 4)
        message_bytes = base64.urlsafe_b64decode(raw)
        message = BytesParser(policy=SMTP).parsebytes(message_bytes)
        recipients = [address for _, address in getaddresses(message.get_all("To", [])) if address]
        if not recipients:
            raise ValueError("В MIME-письме отсутствуют получатели в заголовке To")

        sender = config.SMTP_FROM or config.SMTP_USERNAME
        if "From" not in message:
            message["From"] = sender
        password = config.SMTP_APP_PASSWORD.replace(" ", "")
        if config.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, timeout={timeout!r},
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout={timeout!r})
        with server as client:
            if config.SMTP_STARTTLS:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            client.login(config.SMTP_USERNAME, password)
            refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        sent.append({{"json": {{"accepted": recipients}}}})
    return sent
'''
    return source, set(body.dependencies)
