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


def generate_email_send_node(node: dict[str, Any], function_name: str) -> tuple[str, set[str]]:
    """Создать SMTP-отправитель для стандартного узла n8n Send Email."""
    name = node.get("name", "<без имени>")
    parameters = node.get("parameters", {})
    if not isinstance(parameters, dict):
        raise WorkflowError(f"Узел {name}: параметры Email Send должны быть объектом")

    credentials = node.get("credentials", {})
    smtp_credential = credentials.get("smtp") if isinstance(credentials, dict) else None
    credential_reference = (
        smtp_credential.get("id") or smtp_credential.get("name")
        if isinstance(smtp_credential, dict)
        else None
    )
    if not isinstance(credential_reference, str) or not credential_reference:
        raise WorkflowError(f"Узел {name}: не назначены SMTP credentials (smtp)")

    version = node.get("typeVersion", 2.1)
    if not isinstance(version, (int, float)) or isinstance(version, bool):
        raise WorkflowError(f"Узел {name}: typeVersion Email Send должен быть числом")
    default_format = "html" if version < 2 else "text"
    email_format = parameters.get("emailFormat", default_format)
    if not isinstance(email_format, str) or email_format not in {"text", "html", "both"}:
        raise WorkflowError(f"Узел {name}: неподдерживаемый emailFormat {email_format!r}")

    values: dict[str, str] = {}
    dependencies: set[str] = set()
    parameter_names = ["fromEmail", "toEmail", "subject"]
    if email_format in {"text", "both"}:
        parameter_names.append("text")
    if email_format in {"html", "both"}:
        parameter_names.append("html")
    for parameter in parameter_names:
        value = parameters.get(parameter, "")
        if not isinstance(value, str):
            raise WorkflowError(f"Узел {name}: параметр {parameter} должен быть строкой")
        expression = compile_expression(value, node=name, parameter=parameter)
        values[parameter] = expression.code
        dependencies.update(expression.dependencies)

    options = parameters.get("options", {})
    if not isinstance(options, dict):
        raise WorkflowError(f"Узел {name}: параметр options должен быть объектом")
    supported_options = {
        "appendAttribution",
        "allowUnauthorizedCerts",
        "attachments",
        "fileAttachments",
        "ccEmail",
        "bccEmail",
        "replyTo",
    }
    unsupported_options = set(options) - supported_options
    if unsupported_options:
        raise WorkflowError(
            f"Узел {name}: неподдерживаемые параметры Email Send: "
            f"{', '.join(sorted(unsupported_options))}"
        )

    option_values: dict[str, str] = {}
    for parameter in ("attachments", "fileAttachments", "ccEmail", "bccEmail", "replyTo"):
        value = options.get(parameter, "")
        if not isinstance(value, str):
            raise WorkflowError(f"Узел {name}: параметр options.{parameter} должен быть строкой")
        expression = compile_expression(value, node=name, parameter=f"options.{parameter}")
        option_values[parameter] = expression.code
        dependencies.update(expression.dependencies)

    append_attribution = options.get("appendAttribution", version >= 2.1)
    if not isinstance(append_attribution, bool):
        raise WorkflowError(f"Узел {name}: параметр options.appendAttribution должен быть логическим")
    allow_unauthorized_certs = options.get("allowUnauthorizedCerts", False)
    if not isinstance(allow_unauthorized_certs, bool):
        raise WorkflowError(f"Узел {name}: параметр options.allowUnauthorizedCerts должен быть логическим")

    source = f'''import base64
import json
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses

import config


def _mail_string(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _addresses(value):
    return [address for _, address in getaddresses([_mail_string(value)]) if address]


def _binary_properties(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _binary_content(item, property_name):
    binary = item.get("binary", {{}})
    metadata = binary.get(property_name) if isinstance(binary, dict) else None
    if not isinstance(metadata, dict):
        raise ValueError(f"Не найден binary-параметр {{property_name!r}} для вложения")
    content = metadata.get("data")
    if isinstance(content, bytes):
        return content, metadata
    if isinstance(content, str):
        return base64.b64decode(content), metadata
    raise ValueError(f"Binary-параметр {{property_name!r}} не содержит данных base64 или bytes")


def {function_name}(items, results, execution):
    """Сформировать и отправить письма через SMTP credentials из config.py."""
    if not config.SMTP_HOST:
        raise ValueError("Задайте SMTP_HOST в config.py или переменных окружения")
    if not config.SMTP_USERNAME or not config.SMTP_APP_PASSWORD:
        raise ValueError("Задайте SMTP_USERNAME и SMTP_APP_PASSWORD в config.py или окружении")
    if config.SMTP_USE_SSL and config.SMTP_STARTTLS:
        raise ValueError("Выберите SMTP_USE_SSL или SMTP_STARTTLS, но не оба режима")

    tls_context = (
        ssl._create_unverified_context()
        if {allow_unauthorized_certs!r}
        else ssl.create_default_context()
    )
    sent = []
    for item in items:
        from_email = _mail_string({values['fromEmail']})
        to_email = _mail_string({values['toEmail']})
        subject = _mail_string({values['subject']})
        cc_email = _mail_string({option_values['ccEmail']})
        bcc_email = _mail_string({option_values['bccEmail']})
        reply_to = _mail_string({option_values['replyTo']})
        text_body = _mail_string({values.get('text', "''")})
        html_body = _mail_string({values.get('html', "''")})

        if {append_attribution!r}:
            attribution = "This email was sent automatically with n8n"
            if {email_format!r} in {{"text", "both"}}:
                text_body += "\\n\\n---\\n" + attribution + "\\nhttps://n8n.io"
            if {email_format!r} in {{"html", "both"}}:
                html_body += '<br><br>---<br><em>This email was sent automatically with '
                html_body += '<a href="https://n8n.io" target="_blank">n8n</a></em>'

        message = EmailMessage()
        message["From"] = from_email
        message["To"] = to_email
        message["Subject"] = subject
        if cc_email:
            message["Cc"] = cc_email
        if reply_to:
            message["Reply-To"] = reply_to
        if {email_format!r} == "text":
            message.set_content(text_body)
        elif {email_format!r} == "html":
            message.set_content(html_body, subtype="html")
        else:
            message.set_content(text_body)
            message.add_alternative(html_body, subtype="html")

        inline_names = _binary_properties({option_values['attachments']})
        if inline_names:
            html_part = message.get_body(preferencelist=("html",))
            if html_part is None:
                raise ValueError("Для inline-вложений options.attachments требуется emailFormat html или both")
            for property_name in inline_names:
                content, metadata = _binary_content(item, property_name)
                mime_type = str(metadata.get("mimeType") or "application/octet-stream").split(";", 1)[0]
                maintype, separator, subtype = mime_type.partition("/")
                if not separator:
                    maintype, subtype = "application", "octet-stream"
                html_part.add_related(
                    content,
                    maintype=maintype,
                    subtype=subtype,
                    cid=property_name,
                    filename=metadata.get("fileName") or "unknown",
                )

        for property_name in _binary_properties({option_values['fileAttachments']}):
            content, metadata = _binary_content(item, property_name)
            mime_type = str(metadata.get("mimeType") or "application/octet-stream").split(";", 1)[0]
            maintype, separator, subtype = mime_type.partition("/")
            if not separator:
                maintype, subtype = "application", "octet-stream"
            message.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=metadata.get("fileName") or "unknown",
            )

        recipients = _addresses(to_email) + _addresses(cc_email) + _addresses(bcc_email)
        if not recipients:
            raise ValueError("В Email Send не указан ни один адрес получателя")
        sender_addresses = _addresses(from_email)
        if not sender_addresses:
            raise ValueError("В Email Send не указан корректный адрес отправителя")

        password = config.SMTP_APP_PASSWORD.replace(" ", "")
        if config.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(
                config.SMTP_HOST,
                config.SMTP_PORT,
                timeout=60,
                context=tls_context,
            )
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=60)
        with server as client:
            if config.SMTP_HOSTNAME:
                client.ehlo(config.SMTP_HOSTNAME)
            if config.SMTP_STARTTLS:
                client.starttls(context=tls_context)
                if config.SMTP_HOSTNAME:
                    client.ehlo(config.SMTP_HOSTNAME)
                else:
                    client.ehlo()
            client.login(config.SMTP_USERNAME, password)
            refused = client.send_message(
                message,
                from_addr=sender_addresses[0],
                to_addrs=recipients,
            )
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        sent.append({{"json": {{"accepted": recipients}}}})
    return sent
'''
    return source, dependencies
