"""SMTP 与通道开关。密码只走环境变量,不进 settings.toml。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from notify.channels import select_channels


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    to_addrs: tuple[str, ...]
    use_ssl: bool
    starttls: bool
    timeout: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_addr and self.to_addrs)


@dataclass(frozen=True)
class NotifyConfig:
    channels: tuple[str, ...]
    skipped_channels: tuple[str, ...]
    attach_pdf: bool = True
    attach_html: bool = False
    max_attach_bytes: int = 15 * 1024 * 1024
    smtp: SmtpConfig | None = None

    @property
    def wants_email(self) -> bool:
        return "email" in self.channels


class NotifyConfigError(ValueError):
    pass


def _truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_addrs(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return tuple(p for p in parts if p)


def smtp_from_env(env: dict[str, str] | None = None) -> SmtpConfig | None:
    """读 FISHNET_SMTP_*。缺 host 视为未配置,返回 None(调度里跳过,不算故障)。"""
    src = env if env is not None else os.environ
    host = (src.get("FISHNET_SMTP_HOST") or "").strip()
    if not host:
        return None
    try:
        port = int((src.get("FISHNET_SMTP_PORT") or "465").strip() or "465")
    except ValueError as e:
        raise NotifyConfigError(f"FISHNET_SMTP_PORT 不是整数: {e}") from e
    user = (src.get("FISHNET_SMTP_USER") or "").strip()
    password = (src.get("FISHNET_SMTP_PASSWORD") or "").strip()
    from_addr = (src.get("FISHNET_SMTP_FROM") or user or "").strip()
    to_raw = (src.get("FISHNET_SMTP_TO") or from_addr or "").strip()
    to_addrs = _split_addrs(to_raw)
    starttls_raw = src.get("FISHNET_SMTP_STARTTLS")
    explicit = starttls_raw is not None and starttls_raw.strip() != ""
    if port == 465:
        # 465 是隐式 SMTPS。STARTTLS=1 会改走明文 SMTP(),163/QQ 立刻断开。
        use_ssl = True
        starttls = False
    elif explicit:
        starttls = _truthy(starttls_raw)
        use_ssl = False
    else:
        starttls = port == 587
        use_ssl = False
    if use_ssl and starttls:
        raise NotifyConfigError("SMTP 不能同时 SSL 和 STARTTLS")
    try:
        timeout = float((src.get("FISHNET_SMTP_TIMEOUT") or "30").strip() or "30")
    except ValueError as e:
        raise NotifyConfigError(f"FISHNET_SMTP_TIMEOUT 不是数字: {e}") from e
    return SmtpConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        from_addr=from_addr,
        to_addrs=to_addrs,
        use_ssl=use_ssl,
        starttls=starttls,
        timeout=timeout,
    )


def load_notify_config(
    *,
    settings=None,
    env: dict[str, str] | None = None,
) -> NotifyConfig:
    """settings.toml 的 [notify] + 环境变量。"""
    from core.settings import load_settings

    cfg = settings or load_settings()
    enabled, skipped = select_channels(cfg.notify_channels)
    smtp = smtp_from_env(env)
    return NotifyConfig(
        channels=tuple(enabled),
        skipped_channels=tuple(skipped),
        attach_pdf=cfg.notify_attach_pdf,
        attach_html=cfg.notify_attach_html,
        max_attach_bytes=cfg.notify_max_attach_bytes,
        smtp=smtp,
    )


def require_smtp(cfg: NotifyConfig) -> SmtpConfig:
    smtp = cfg.smtp
    if smtp is None or not smtp.configured:
        raise NotifyConfigError(
            "邮件未配置。在 .env 填写 FISHNET_SMTP_HOST / USER / PASSWORD / TO。"
        )
    if not smtp.to_addrs:
        raise NotifyConfigError("FISHNET_SMTP_TO 为空")
    return smtp


def resolve_edition_dir(
    editions_root: Path,
    *,
    edition: str | None = None,
    directory: Path | None = None,
) -> Path | None:
    """--dir 优先,其次 --edition,否则取最新一期目录名。"""
    if directory is not None:
        dest = Path(directory)
        return dest if dest.is_dir() else None
    root = Path(editions_root)
    if edition:
        dest = root / edition
        return dest if dest.is_dir() else None
    if not root.is_dir():
        return None
    dirs = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")),
        key=lambda p: p.name,
        reverse=True,
    )
    return dirs[0] if dirs else None
