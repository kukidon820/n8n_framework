import base64
import sys
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from types import SimpleNamespace

import pytest

from n8n_transcriper.converters.smtp import generate_smtp_node, is_gmail_send_node
from n8n_transcriper.generator import generate_project


def gmail_send_node():
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
