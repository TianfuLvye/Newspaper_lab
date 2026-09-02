"""把一期报纸推到已启用的通道。目前只有 email。"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from notify.channels import UnknownChannelError
from notify.compose import compose_digest
from notify.config import (
    NotifyConfig,
    NotifyConfigError,
    load_notify_config,
    require_smtp,
)
from notify.email import build_message, send_message

log = logging.getLogger("fishnet.notify")

RECORD_NAME = "notify.json"

SendFn = Callable[..., str]


@dataclass
class PushResult:
    edition_id: str
    status: str  # sent / skipped / dry-run / failed
    channels: list[str] = field(default_factory=list)
    skipped_channels: list[str] = field(default_factory=list)
    to: list[str] = field(default_factory=list)
    subject: str = ""
    attachments: list[str] = field(default_factory=list)
    message_id: str = ""
    reason: str = ""
    record_path: Path | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_record(edition_dir: Path) -> dict | None:
    path = edition_dir / RECORD_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_record(edition_dir: Path, payload: dict) -> Path:
    path = edition_dir / RECORD_NAME
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def already_sent(record: dict | None, channel: str) -> bool:
    if not record:
        return False
    sent = record.get("channels") or []
    return channel in sent and str(record.get("status") or "") == "sent"


def push_edition_dir(
    edition_dir: Path,
    *,
    config: NotifyConfig | None = None,
    dry_run: bool = False,
    force: bool = False,
    send_fn: SendFn | None = None,
) -> PushResult:
    dest = Path(edition_dir)
    if not dest.is_dir():
        return PushResult(
            edition_id=dest.name,
            status="failed",
            reason=f"找不到期次目录 {dest}",
        )
    try:
        cfg = config or load_notify_config()
    except (NotifyConfigError, UnknownChannelError) as e:
        return PushResult(edition_id=dest.name, status="failed", reason=str(e))

    if not cfg.channels:
        return PushResult(
            edition_id=dest.name,
            status="skipped",
            skipped_channels=list(cfg.skipped_channels),
            reason="没有已实现的推送通道(当前只支持 email)",
        )

    mail = compose_digest(
        dest,
        attach_pdf=cfg.attach_pdf,
        attach_html=cfg.attach_html,
        max_attach_bytes=cfg.max_attach_bytes,
    )
    result = PushResult(
        edition_id=mail.edition_id,
        status="skipped",
        channels=list(cfg.channels),
        skipped_channels=list(cfg.skipped_channels),
        subject=mail.subject,
        attachments=[p.name for p in mail.attachments],
    )

    record = load_record(dest)
    if not force and not dry_run and already_sent(record, "email"):
        result.status = "skipped"
        result.reason = f"已推送过(见 {RECORD_NAME}),需要重发请加 --force"
        result.to = list(record.get("to") or []) if record else []
        result.message_id = str(record.get("message_id") or "") if record else ""
        return result

    if "email" not in cfg.channels:
        result.reason = "channels 未包含 email"
        return result

    try:
        smtp = require_smtp(cfg) if not dry_run else cfg.smtp
    except NotifyConfigError as e:
        result.status = "skipped"
        result.reason = str(e)
        return result

    if dry_run:
        result.status = "dry-run"
        result.to = list(smtp.to_addrs) if smtp else []
        result.reason = "dry-run,未连接 SMTP"
        if smtp is None:
            result.reason = "dry-run,SMTP 未配置(仍写出了摘要)"
        return result

    assert smtp is not None
    msg = build_message(mail, smtp)
    sender = send_fn or send_message
    try:
        message_id = sender(smtp, msg)
    except Exception as e:
        log.exception("smtp send failed edition=%s", mail.edition_id)
        result.status = "failed"
        result.reason = repr(e)
        result.to = list(smtp.to_addrs)
        return result

    payload = {
        "edition_id": mail.edition_id,
        "status": "sent",
        "channels": ["email"],
        "skipped_channels": list(cfg.skipped_channels),
        "to": list(smtp.to_addrs),
        "subject": mail.subject,
        "attachments": [p.name for p in mail.attachments],
        "message_id": message_id,
        "sent_at": _now_iso(),
    }
    record_path = _write_record(dest, payload)
    result.status = "sent"
    result.to = list(smtp.to_addrs)
    result.message_id = message_id
    result.record_path = record_path
    result.reason = ""
    log.info(
        "pushed edition=%s channel=email to=%s attach=%s",
        mail.edition_id,
        ",".join(smtp.to_addrs),
        ",".join(p.name for p in mail.attachments) or "-",
    )
    return result
