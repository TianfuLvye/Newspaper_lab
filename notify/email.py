"""SMTP 发送。只用标准库,不引入第三方邮件 SDK。"""
from __future__ import annotations

import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from notify.compose import DigestMail
from notify.config import SmtpConfig

log = logging.getLogger("fishnet.notify.email")

_ATTACH_NAME = {
    "digest.pdf": "自动日报.pdf",
    "digest.html": "自动日报.html",
}


def build_message(
    mail: DigestMail,
    smtp: SmtpConfig,
    *,
    extra_headers: dict[str, str] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = mail.subject
    msg["From"] = smtp.from_addr
    msg["To"] = ", ".join(smtp.to_addrs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="fishnet.local")
    msg["X-Fishnet-Edition"] = mail.edition_id
    if extra_headers:
        for key, value in extra_headers.items():
            msg[key] = value
    msg.set_content(mail.text, charset="utf-8")
    msg.add_alternative(mail.html, subtype="html", charset="utf-8")
    for path in mail.attachments:
        _attach_file(msg, path, edition_id=mail.edition_id)
    return msg


def _attach_file(msg: EmailMessage, path: Path, *, edition_id: str) -> None:
    data = path.read_bytes()
    ctype, encoding = mimetypes.guess_type(path.name)
    if ctype is None or encoding is not None:
        maintype, subtype = "application", "octet-stream"
    else:
        maintype, subtype = ctype.split("/", 1)
    filename = _ATTACH_NAME.get(path.name, path.name)
    if path.suffix.lower() == ".pdf":
        filename = f"自动日报-{edition_id}.pdf"
    elif path.suffix.lower() in {".html", ".htm"}:
        filename = f"自动日报-{edition_id}.html"
    msg.add_attachment(
        data,
        maintype=maintype,
        subtype=subtype,
        filename=filename,
    )


def send_message(smtp: SmtpConfig, msg: EmailMessage) -> str:
    """发出去,返回 Message-ID。失败向上抛,由调用方记日志。"""
    if smtp.use_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            smtp.host, smtp.port, timeout=smtp.timeout
        )
    else:
        client = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout)
    try:
        client.ehlo()
        if smtp.starttls:
            client.starttls()
            client.ehlo()
        if smtp.user:
            client.login(smtp.user, smtp.password)
        refused = client.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        try:
            client.quit()
        except Exception:
            log.debug("smtp quit failed", exc_info=True)
    return str(msg.get("Message-ID") or "")
