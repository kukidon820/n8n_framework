"""Проверки отправки почты через SMTP и Gmail API.

Проверяются MIME-сообщения, адресаты и вложения без отправки реальных писем.
"""

import base64
import sys
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.smtp import (
    generate_email_send_node,
    generate_smtp_node,
    is_gmail_send_node,
)
from n8n_transcriper.generator import generate_project


def gmail_send_node():
    """Сформировать Gmail Send node с тестовым получателем и binary-вложением."""
    return {
        "id": "send-mail",
        "name": "HTTP - Send email with ZIP",
        "type": "n8n-nodes-base.httpRequest",
        "parameters": {
            "method": "POST",
            "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "gmailOAuth2",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({raw: $json.raw}) }}",
            "options": {"timeout": 60000},
        },
    }


def email_send_node():
    """Сформировать emailSend node с SMTP credentials и полями сообщения."""
    return {
        "id": "email-send-1",
        "name": "Send Email",
        "type": "n8n-nodes-base.emailSend",
        "typeVersion": 2.1,
        "parameters": {
            "fromEmail": "reports@example.com",
            "toEmail": "reader@example.com",
            "subject": "Daily report",
            "emailFormat": "text",
            "text": "Report is ready",
            "options": {
                "ccEmail": "copy@example.com",
                "bccEmail": "hidden@example.com",
                "replyTo": "reply@example.com",
                "fileAttachments": "archive",
            },
        },
        "credentials": {"smtp": {"id": "smtp-1", "name": "SMTP account"}},
    }


def test_email_send_generates_smtp_config_and_module(tmp_path):
    node = email_send_node()
    workflow_dir = tmp_path / "generated"

    generate_project({"nodes": [node], "connections": {}}, workflow_dir)

    source = workflow_dir / "email_senders" / "send_email.py"
    config_text = (workflow_dir / "config.py").read_text(encoding="utf-8")
    readme = (workflow_dir / "README.md").read_text(encoding="utf-8")
    assert source.is_file()
    assert 'SMTP_HOST = os.getenv("SMTP_HOST", "")' in config_text
    assert 'SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")' in config_text
    assert 'SMTP_HOSTNAME = os.getenv("SMTP_HOSTNAME", "")' in config_text
    assert "SMTP_HOST" in readme


def test_email_send_translates_expressions_and_sends_attachments(monkeypatch):
    node = email_send_node()
    node["parameters"]["toEmail"] = "={{ $('Settings').first().json.recipient }}"
    node["parameters"]["text"] = "={{ $json.body }}"
    source, references = generate_email_send_node(node, "send_email")
    assert references == {"Settings"}

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.connection = (host, port, timeout, context)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            self.tls_context = context

        def ehlo(self, hostname=None):
            self.hostname = hostname

        def login(self, username, password):
            self.credentials = (username, password)

        def send_message(self, message, *, from_addr, to_addrs):
            sent.append((self.connection, self.credentials, message, from_addr, to_addrs))
            return {}

    class SMTPRecipientsRefused(Exception):
        pass

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(
        SMTP_HOST="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USERNAME="sender@example.com",
        SMTP_APP_PASSWORD="abcd efgh ijkl mnop",
        SMTP_HOSTNAME="",
        SMTP_USE_SSL=False,
        SMTP_STARTTLS=True,
    ))
    monkeypatch.setitem(sys.modules, "smtplib", SimpleNamespace(
        SMTP=FakeSMTP,
        SMTP_SSL=FakeSMTP,
        SMTPRecipientsRefused=SMTPRecipientsRefused,
    ))
    namespace = {}
    exec(source, namespace)
    result = namespace["send_email"](
        [{
            "json": {"body": "Report is ready"},
            "binary": {
                "archive": {
                    "data": base64.b64encode(b"zip bytes").decode("ascii"),
                    "fileName": "report.zip",
                    "mimeType": "application/zip",
                },
            },
        }],
        {"Settings": [{"json": {"recipient": "reader@example.com"}}]},
        {},
    )

    assert result == [{"json": {"accepted": [
        "reader@example.com", "copy@example.com", "hidden@example.com",
    ]}}]
    connection, credentials, message, from_addr, recipients = sent[0]
    assert connection[:3] == ("smtp.example.com", 587, 60)
    assert credentials == ("sender@example.com", "abcdefghijklmnop")
    assert from_addr == "reports@example.com"
    assert recipients == ["reader@example.com", "copy@example.com", "hidden@example.com"]
    assert message["From"] == "reports@example.com"
    assert message["To"] == "reader@example.com"
    assert message["Cc"] == "copy@example.com"
    assert message["Bcc"] is None
    assert message["Reply-To"] == "reply@example.com"
    assert "This email was sent automatically with n8n" in message.get_body(preferencelist=("plain",)).get_content()
    assert any(part.get_filename() == "report.zip" and part.get_content() == b"zip bytes" for part in message.walk())


def test_email_send_rejects_unknown_options_and_missing_credentials():
    node = email_send_node()
    node["parameters"]["options"]["unsupportedOption"] = True
    with pytest.raises(ValueError, match="неподдерживаемые параметры Email Send"):
        generate_email_send_node(node, "send_email")

    node = email_send_node()
    node.pop("credentials")
    with pytest.raises(ValueError, match="не назначены SMTP credentials"):
        generate_email_send_node(node, "send_email")


def test_email_send_rejects_multiple_smtp_credentials_in_one_project(tmp_path):
    first = email_send_node()
    second = email_send_node()
    second["id"] = "email-send-2"
    second["name"] = "Send Email 2"
    second["credentials"]["smtp"]["id"] = "smtp-account-2"
    workflow = {
        "nodes": [first, second],
        "connections": {
            "Send Email": {"main": [[{"node": "Send Email 2", "type": "main", "index": 0}]]},
        },
    }

    with pytest.raises(ValueError, match="несколько SMTP credentials"):
        generate_project(workflow, tmp_path / "generated")


def test_gmail_send_endpoint_generates_smtp_config_and_sender(tmp_path, monkeypatch):
    node = gmail_send_node()
    assert is_gmail_send_node(node)
    source, references = generate_smtp_node(node, "send_email")
    assert references == set()

    workflow_dir = tmp_path / "generated"
    generate_project({"nodes": [node], "connections": {}}, workflow_dir)
    config_text = (workflow_dir / "config.py").read_text(encoding="utf-8")
    assert 'SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")' in config_text
    assert 'SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))' in config_text
    assert 'SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")' in config_text
    assert "GMAIL_CLIENT_SECRET" not in config_text
    assert "SMTP_APP_PASSWORD" in (workflow_dir / "README.md").read_text(encoding="utf-8")

    outgoing = EmailMessage(policy=SMTP_POLICY)
    outgoing["To"] = "recipient@example.com"
    outgoing["Subject"] = "CSV report"
    outgoing.set_content("Report body")
    outgoing.add_attachment(b"zip contents", maintype="application", subtype="zip", filename="tasks.zip")
    raw = base64.urlsafe_b64encode(outgoing.as_bytes()).decode("ascii").rstrip("=")

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.connection = (host, port, timeout, context)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            self.tls_context = context

        def ehlo(self):
            pass

        def login(self, username, password):
            self.credentials = (username, password)

        def send_message(self, message, *, from_addr, to_addrs):
            sent.append((self.connection, self.credentials, message, from_addr, to_addrs))
            return {}

    class SMTPRecipientsRefused(Exception):
        pass

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(
        SMTP_USERNAME="sender@gmail.com",
        SMTP_APP_PASSWORD="abcd efgh ijkl mnop",
        SMTP_FROM="",
        SMTP_HOST="smtp.gmail.com",
        SMTP_PORT=587,
        SMTP_USE_SSL=False,
        SMTP_STARTTLS=True,
    ))
    monkeypatch.setitem(sys.modules, "smtplib", SimpleNamespace(
        SMTP=FakeSMTP,
        SMTP_SSL=FakeSMTP,
        SMTPRecipientsRefused=SMTPRecipientsRefused,
    ))
    namespace = {}
    exec(source, namespace)
    result = namespace["send_email"]([{"json": {"raw": raw}}], {}, {})

    assert result == [{"json": {"accepted": ["recipient@example.com"]}}]
    connection, credentials, message, from_addr, recipients = sent[0]
    assert connection[:3] == ("smtp.gmail.com", 587, 60.0)
    assert credentials == ("sender@gmail.com", "abcdefghijklmnop")
    assert message["From"] == "sender@gmail.com"
    assert from_addr == "sender@gmail.com"
    assert recipients == ["recipient@example.com"]
    assert any(part.get_filename() == "tasks.zip" for part in message.walk())


def test_other_gmail_api_endpoints_remain_oauth_http_nodes():
    node = gmail_send_node()
    node["parameters"]["url"] = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    assert not is_gmail_send_node(node)


def test_smtp_sender_rejects_unencrypted_app_password(monkeypatch):
    source, _ = generate_smtp_node(gmail_send_node(), "send_email")
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(
        SMTP_USERNAME="sender@gmail.com",
        SMTP_APP_PASSWORD="app-password",
        SMTP_FROM="",
        SMTP_HOST="smtp.gmail.com",
        SMTP_PORT=587,
        SMTP_USE_SSL=False,
        SMTP_STARTTLS=False,
    ))
    namespace = {}
    exec(source, namespace)
    with pytest.raises(ValueError, match="SMTP требует шифрование"):
        namespace["send_email"]([{"json": {"raw": "unused"}}], {}, {})
