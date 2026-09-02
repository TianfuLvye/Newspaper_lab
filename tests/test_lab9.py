"""Lab 9 验收:通道选择 + 邮件组装。不连真实 SMTP。"""
from __future__ import annotations

import json
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import load_settings
from notify.channels import UnknownChannelError, select_channels
from notify.compose import compose_digest
from notify.config import NotifyConfig, SmtpConfig, smtp_from_env
from notify.email import build_message
from notify.push import already_sent, push_edition_dir

PASS = FAIL = 0
DOC = ROOT / "docs" / "lab-09-notify.md"
ADR = ROOT / "docs" / "adr" / "010-notify-email.md"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def _tiny_edition(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    articles = [
        {
            "id": "lede",
            "title": "今日综述",
            "markdown": "早餐三件事：市场、政策、科技。",
            "metadata": {"section": "lede"},
        },
        {
            "id": "am01",
            "title": "头版一条很长的新闻标题",
            "markdown": "这是摘要第一段，用来扫读。\n\n后面还有很多正文。",
            "metadata": {"section": "headline"},
        },
        {
            "id": "am02",
            "title": "深度稿",
            "markdown": "深度阅读的第一句。",
            "metadata": {"section": "deepread"},
        },
        {
            "id": "health",
            "title": "系统体检",
            "markdown": "磁盘还好。",
            "metadata": {"section": "health"},
        },
    ]
    (dest / "articles.json").write_text(
        json.dumps(articles, ensure_ascii=False), encoding="utf-8"
    )
    (dest / "digest.md").write_text("# 早报\n\n## F01 · 头版一条很长的新闻标题\n", encoding="utf-8")
    (dest / "digest.pdf").write_bytes(b"%PDF-1.4 fake-pdf")
    (dest / "digest.html").write_text(
        "<html><style>:root{--paper-width:297mm}</style></html>",
        encoding="utf-8",
    )
    return dest


def _smtp() -> SmtpConfig:
    return SmtpConfig(
        host="smtp.test.local",
        port=465,
        user="fishnet@test.local",
        password="secret",
        from_addr="fishnet@test.local",
        to_addrs=("reader@test.local",),
        use_ssl=True,
        starttls=False,
    )


print("\n[Lab 9] 文档")
check("lab-09 笔记存在", DOC.exists())
check("ADR-010 存在", ADR.exists())
if DOC.exists():
    t = DOC.read_text(encoding="utf-8")
    check("笔记写了 SMTP / digest.pdf", "SMTP" in t and "digest.pdf" in t)
    check("笔记写了不内联 A3 HTML", "内联" in t or "digest.html" in t)
if ADR.exists():
    adr = ADR.read_text(encoding="utf-8")
    check("ADR 选择邮件为主通道", "邮件" in adr and "主通道" in adr)


print("\n[Lab 9] 通道选择")
en, sk = select_channels(["email"])
check("默认实现 email", en == ["email"] and sk == [])
en, sk = select_channels([])
check("空配置回落到 email", en == ["email"])
en, sk = select_channels(["email", "telegram", "feishu"])
check("telegram/飞书记为跳过", en == ["email"] and sk == ["telegram", "feishu"], str((en, sk)))
en, sk = select_channels(["telegram"])
check("只配未实现通道则 enabled 空", en == [] and sk == ["telegram"], str((en, sk)))
try:
    select_channels(["carrier-pigeon"])
    check("未知通道报错", False)
except UnknownChannelError:
    check("未知通道报错", True)


print("\n[Lab 9] SMTP 配置")
check("没 host 就是未配置", smtp_from_env({}) is None)
s = smtp_from_env(
    {
        "FISHNET_SMTP_HOST": "smtp.qq.com",
        "FISHNET_SMTP_PORT": "465",
        "FISHNET_SMTP_USER": "a@qq.com",
        "FISHNET_SMTP_PASSWORD": "auth",
        "FISHNET_SMTP_TO": "a@qq.com, b@qq.com",
    }
)
check("465 走 SSL", s is not None and s.use_ssl and not s.starttls)
s465_bad = smtp_from_env(
    {
        "FISHNET_SMTP_HOST": "smtp.163.com",
        "FISHNET_SMTP_PORT": "465",
        "FISHNET_SMTP_USER": "a@163.com",
        "FISHNET_SMTP_PASSWORD": "auth",
        "FISHNET_SMTP_TO": "a@163.com",
        "FISHNET_SMTP_STARTTLS": "1",
    }
)
check(
    "465 忽略 STARTTLS 仍走 SSL",
    s465_bad is not None and s465_bad.use_ssl and not s465_bad.starttls,
    str(s465_bad),
)
check("收件人可逗号分隔", s is not None and s.to_addrs == ("a@qq.com", "b@qq.com"), str(s))
s587 = smtp_from_env(
    {
        "FISHNET_SMTP_HOST": "smtp.gmail.com",
        "FISHNET_SMTP_PORT": "587",
        "FISHNET_SMTP_USER": "a@gmail.com",
        "FISHNET_SMTP_FROM": "a@gmail.com",
        "FISHNET_SMTP_TO": "a@gmail.com",
        "FISHNET_SMTP_PASSWORD": "x",
    }
)
check("587 走 STARTTLS", s587 is not None and s587.starttls and not s587.use_ssl)

cfg = load_settings()
check("settings 默认通道 email", cfg.notify_channels == ("email",), str(cfg.notify_channels))
check("默认附 PDF 不附 HTML", cfg.notify_attach_pdf and not cfg.notify_attach_html)


print("\n[Lab 9] 邮件组装")
with tempfile.TemporaryDirectory() as tmp:
    dest = _tiny_edition(Path(tmp) / "2026-09-02-am")
    mail = compose_digest(dest, attach_pdf=True, attach_html=False)
    check("主题含早报和日期", "早报" in mail.subject and "2026-09-02" in mail.subject, mail.subject)
    titles = [s.title for s in mail.stories]
    check("头版进正文", "头版一条很长的新闻标题" in titles, str(titles))
    check("综述进正文", any(s.section == "lede" for s in mail.stories))
    check("体检不进正文", all(s.section != "health" for s in mail.stories), str(titles))
    check("摘要截过第一段", any("摘要第一段" in s.summary for s in mail.stories), str(mail.stories))
    check("附上 PDF", [p.name for p in mail.attachments] == ["digest.pdf"], str(mail.attachments))
    check("HTML 正文不是 A3 报纸", "--paper-width" not in mail.html and "297mm" not in mail.html)
    check("纯文本也能扫读", "头版一条很长的新闻标题" in mail.text)

    msg = build_message(mail, _smtp())
    check("From/To 写上", msg["From"] == "fishnet@test.local" and "reader@test.local" in msg["To"])
    check("有 HTML alternative", msg.is_multipart())
    payloads = []
    filenames = []
    for part in msg.walk():
        payloads.append(part.get_content_type())
        name = part.get_filename()
        if name:
            filenames.append(name)
    check("含 text/plain 与 text/html", "text/plain" in payloads and "text/html" in payloads, str(payloads))
    check("附件文件名带期号", any("2026-09-02-am" in n and n.endswith(".pdf") for n in filenames), str(filenames))
    check("默认不附 digest.html", not any(n.endswith(".html") for n in filenames), str(filenames))


print("\n[Lab 9] 发送编排(假 SMTP)")
with tempfile.TemporaryDirectory() as tmp:
    dest = _tiny_edition(Path(tmp) / "2026-09-02-pm")
    sent: list[EmailMessage] = []

    def fake_send(smtp: SmtpConfig, msg: EmailMessage) -> str:
        sent.append(msg)
        return msg["Message-ID"] or "<fake@test>"

    notify_cfg = NotifyConfig(
        channels=("email",),
        skipped_channels=("telegram",),
        smtp=_smtp(),
    )
    r = push_edition_dir(dest, config=notify_cfg, send_fn=fake_send)
    check("第一次发出去", r.status == "sent", r.reason)
    check("假 SMTP 被调用一次", len(sent) == 1, str(len(sent)))
    check("写下 notify.json", (dest / "notify.json").is_file())
    rec = json.loads((dest / "notify.json").read_text(encoding="utf-8"))
    check("记录通道 email", rec.get("channels") == ["email"])
    check("already_sent", already_sent(rec, "email"))

    r2 = push_edition_dir(dest, config=notify_cfg, send_fn=fake_send)
    check("第二次默认跳过", r2.status == "skipped" and len(sent) == 1, r2.reason)
    r3 = push_edition_dir(dest, config=notify_cfg, send_fn=fake_send, force=True)
    check("--force 再发", r3.status == "sent" and len(sent) == 2, r3.reason)

    r_dry = push_edition_dir(dest, config=notify_cfg, dry_run=True, send_fn=fake_send)
    check("dry-run 不连 SMTP", r_dry.status == "dry-run" and len(sent) == 2, r_dry.status)

    bare = NotifyConfig(channels=("email",), skipped_channels=(), smtp=None)
    r_skip = push_edition_dir(dest, config=bare, force=True)
    check("没配 SMTP 跳过不算失败", r_skip.status == "skipped", r_skip.status)


print("\n[Lab 9] CLI help")
import argparse

from main import build_parser

p = build_parser()
check("help 仍列出 push", "push" in p.format_help())
sub = None
for action in p._subparsers._group_actions:  # type: ignore[attr-defined]
    if isinstance(action, argparse._SubParsersAction):
        sub = action.choices.get("push")
        break
check("push 子命令有 --dry-run", sub is not None and "--dry-run" in sub.format_help())


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
