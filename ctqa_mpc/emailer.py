"""SMTP + Google Chat / Slack / Teams / Discord webhooks (Winston-Lutz model)."""

from __future__ import annotations

import html
import json
import logging
import platform
import smtplib
import sys
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import __version__
from .app_settings import (
    CHAT_CHANNELS,
    ERROR_EMAIL_TO_KEY,
    EVENT_EMAIL_TO_KEY,
    NEW_CASE_EMAIL_TO_KEY,
    chat_webhook_urls,
    email_settings,
    load_settings,
)

logger = logging.getLogger(__name__)
_sending_error_email = False

CHAT_CHANNEL_LABELS = {
    "google_chat": "Google Chat",
    "slack": "Slack",
    "microsoft_teams": "Microsoft Teams",
    "discord": "Discord",
}


def error_email_to_list(value) -> list[str]:
    chunks: list[str] = []
    if isinstance(value, (list, tuple)):
        chunks.extend(str(item or "") for item in value)
    elif value is not None:
        chunks.append(str(value))
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in chunk.replace(";", "\n").splitlines():
            for part in line.split(","):
                name = part.strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
    return out


def format_error_email_to(value) -> str:
    return "\n".join(error_email_to_list(value))


def recipient_addresses(to: str | list | None, domain: str = "") -> list[str]:
    domain = str(domain or "").strip().lstrip("@")
    out: list[str] = []
    for name in error_email_to_list(to):
        if "@" in name:
            out.append(name)
        elif domain:
            out.append(f"{name}@{domain}")
    return out


def merge_recipients(*groups) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for name in error_email_to_list(group):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def send(
    from_user: str,
    from_enc_pw: str,
    to: str | list | None,
    subject: str,
    body: str,
    domain: str,
    host: str,
    port: int,
    enable_ssl: bool,
) -> None:
    if not from_user or not host:
        logger.info("email skipped: missing from/host")
        return
    addresses = recipient_addresses(to, domain)
    if not addresses:
        logger.info("email skipped: no recipients")
        return
    from_addr = from_user if "@" in from_user else f"{from_user}@{domain}"
    msg = MIMEMultipart("related")
    msg.attach(MIMEText(body, "html"))
    msg["From"] = from_addr
    msg["To"] = ", ".join(addresses)
    msg["Subject"] = subject
    smtp = smtplib.SMTP(host, int(port or 25), timeout=30)
    try:
        if enable_ssl:
            smtp.starttls()
        if str(from_enc_pw or "").strip():
            logger.warning("encrypted SMTP password is set but decrypt is not configured; sending without login")
        smtp.sendmail(from_addr, addresses, msg.as_string())
    finally:
        smtp.quit()


def send_from_settings(
    to: str | list | None,
    subject: str,
    body: str,
    data: dict | None = None,
) -> None:
    cfg = email_settings(data)
    send(
        from_user=str(cfg.get("email_from") or ""),
        from_enc_pw=str(cfg.get("email_from_enc_pw") or ""),
        to=to,
        subject=subject,
        body=body,
        domain=str(cfg.get("email_domain") or ""),
        host=str(cfg.get("email_host_address") or ""),
        port=int(cfg.get("email_host_port") or 25),
        enable_ssl=bool(cfg.get("enable_ssl")),
    )


def _post_webhook(url: str, payload: dict) -> None:
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()[:300]
        except Exception:
            pass
        extra = f": {detail}" if detail else ""
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}{extra}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"webhook failed: {exc.reason}") from exc


def _chat_payload(channel: str, text: str) -> dict:
    if channel == "discord":
        clipped = text if len(text) <= 1900 else text[:1900] + "\n…"
        return {"content": clipped}
    clipped = text if len(text) <= 3500 else text[:3500] + "\n…"
    return {"text": clipped}


def post_chat_webhooks(message: str, details: str = "", *, context: str = "", data: dict | None = None) -> None:
    from .app_settings import chat_webhook_urls as urls_of

    text = (
        f"CTQA-MPC {__version__}\n"
        f"host={platform.node()}\n"
        f"context={context or ''}\n"
        f"{message or ''}\n\n"
        f"{details or ''}"
    )
    hooks = urls_of(data)
    for name in CHAT_CHANNELS:
        url = hooks.get(name) or ""
        if url:
            try:
                _post_webhook(url, _chat_payload(name, text))
            except Exception:
                logger.warning("chat webhook %s failed", name, exc_info=True)


def send_test_email(data: dict | None = None) -> None:
    cfg = email_settings(data)
    to = cfg.get(ERROR_EMAIL_TO_KEY)
    if not to:
        raise RuntimeError("Fill error_email_to, email_from, and email_host_address first.")
    send_from_settings(
        to,
        "CTQA-MPC notification test",
        "<html><body><pre>This is a configuration test from Settings → Notifications.</pre></body></html>",
        data,
    )


def send_test_chat(channel: str, data: dict | None = None) -> None:
    label = CHAT_CHANNEL_LABELS.get(channel, channel)
    url = (chat_webhook_urls(data).get(channel) or "").strip()
    if not url:
        raise RuntimeError(f"Enter a {label} webhook_url first.")
    text = (
        f"CTQA-MPC {__version__} notification test\n"
        f"host={platform.node()}\n"
        f"channel={label}\n"
        "This is a configuration test from Settings → Notifications."
    )
    _post_webhook(url, _chat_payload(channel, text))


def send_event(subject: str, body: str, data: dict | None = None) -> None:
    cfg = email_settings(data)
    to = cfg.get(EVENT_EMAIL_TO_KEY)
    if to:
        send_from_settings(to, subject, f"<html><body><pre>{html.escape(body)}</pre></body></html>", data)
    post_chat_webhooks(subject, body, context="event", data=data)


def send_new_case_report(subject: str, html_body: str, extra_to=None, data: dict | None = None) -> None:
    cfg = email_settings(data)
    to = merge_recipients(cfg.get(NEW_CASE_EMAIL_TO_KEY), extra_to)
    if to:
        send_from_settings(to, subject, html_body, data)


def send_list_email(
    to,
    message: str,
    details: str = "",
    *,
    context: str = "event",
    subject: str | None = None,
    blocking: bool = True,
    data: dict | None = None,
) -> None:
    """Send *message* to an explicit address list using clinic SMTP."""
    addresses = error_email_to_list(to)
    if not addresses:
        return
    subj = subject or f"CTQA-MPC event: {(message or context)[:120]}"
    body = (
        "<html><body>"
        f"<pre>{html.escape(f'CTQA-MPC {__version__} host={platform.node()} context={context}')}</pre>"
        f"<pre>{html.escape(message)}\n\n{html.escape(details)}</pre>"
        "</body></html>"
    )
    send_from_settings(addresses, subj, body, data)


def send_error_email(
    message: str,
    details: str = "",
    *,
    context: str = "",
    blocking: bool = True,
) -> None:
    global _sending_error_email
    if _sending_error_email:
        return
    _sending_error_email = True
    try:
        data = load_settings()
        cfg = email_settings(data)
        to = cfg.get(ERROR_EMAIL_TO_KEY)
        text = f"{message}\n\n{details}"
        if to:
            body = (
                "<html><body>"
                f"<pre>{html.escape(f'CTQA-MPC {__version__} host={platform.node()} context={context}')}</pre>"
                f"<pre>{html.escape(text)}</pre>"
                "</body></html>"
            )
            send_from_settings(to, f"CTQA-MPC error ({context or 'app'})", body, data)
        post_chat_webhooks(message, details, context=context, data=data)
    except Exception:
        logger.warning("error notification failed", exc_info=True)
    finally:
        _sending_error_email = False


class _ErrorEmailHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        if record.name.startswith("smtplib"):
            return
        try:
            details = self.format(record)
            if record.exc_info:
                details += "\n" + "".join(traceback.format_exception(*record.exc_info))
            send_error_email(record.getMessage(), details, context=record.name)
        except Exception:
            pass


def install_error_email_hooks() -> None:
    root = logging.getLogger()
    if any(isinstance(h, _ErrorEmailHandler) for h in root.handlers):
        return
    handler = _ErrorEmailHandler()
    handler.setLevel(logging.ERROR)
    root.addHandler(handler)

    def hook(exc_type, exc, tb):
        send_error_email(
            str(exc),
            "".join(traceback.format_exception(exc_type, exc, tb)),
            context="sys.excepthook",
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def send_report_file(report_html, machine_name: str, extra_to=None, data: dict | None = None) -> None:
    from pathlib import Path

    from .identity import profile_email, subscribers_for_new_qa_case

    body = Path(report_html).read_text(encoding="utf-8")
    extra = merge_recipients(
        extra_to,
        [profile_email(p) for p in subscribers_for_new_qa_case(machine_name)],
    )
    send_new_case_report(f"CTQA ({machine_name})", body, extra_to=extra, data=data)
