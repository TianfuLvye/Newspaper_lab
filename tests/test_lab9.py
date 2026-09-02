"""Lab 9 验收:通道选择 + 邮件组装 + Compose 全家桶结构。不连真实 SMTP / 不强制 docker up。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import (
    load_feeds,
    load_settings,
    rewrite_wewe_loopback,
    url_from_env,
)
from notify.channels import UnknownChannelError, select_channels
from notify.compose import compose_digest
from notify.config import NotifyConfig, SmtpConfig, smtp_from_env
from notify.email import build_message
from notify.push import already_sent, push_edition_dir

PASS = FAIL = 0
DOC = ROOT / "docs" / "lab-09-notify.md"
ADR = ROOT / "docs" / "adr" / "010-notify-email.md"
COMPOSE_DOC = ROOT / "docs" / "lab-09-compose.md"
ADR011 = ROOT / "docs" / "adr" / "011-compose-runtime.md"
COMPOSE_YML = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERIGNORE = ROOT / ".dockerignore"
README = ROOT / "README.md"


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


print("\n[Lab 9.2] Compose 全家桶")
check("lab-09-compose 笔记存在", COMPOSE_DOC.exists())
check("ADR-011 存在", ADR011.exists())
check("Dockerfile 存在", DOCKERFILE.exists())
check("entrypoint 存在", ENTRYPOINT.exists())
check(".dockerignore 存在", DOCKERIGNORE.exists())
if COMPOSE_DOC.exists():
    t = COMPOSE_DOC.read_text(encoding="utf-8")
    check("compose 笔记写了 bind mount / 冷启动", "data" in t and "冷启动" in t)
if ADR011.exists():
    adr = ADR011.read_text(encoding="utf-8")
    check("ADR-011 写了环境变量覆盖 URL", "FISHNET_DAILYHOT_URL" in adr or "环境变量" in adr)
if README.exists():
    readme = README.read_text(encoding="utf-8")
    check("README 有 docker compose up -d", "docker compose up -d" in readme)
    check("README 写了部署步骤", "部署" in readme and "data" in readme)
if DOCKERFILE.exists():
    df = DOCKERFILE.read_text(encoding="utf-8")
    check("镜像装 Chromium", "playwright" in df and "chromium" in df.lower())
    check("镜像有 CJK 字体", "fonts-noto-cjk" in df)
    check("镜像入口是 entrypoint", "entrypoint.sh" in df)
if ENTRYPOINT.exists():
    ep = ENTRYPOINT.read_text(encoding="utf-8")
    check("entrypoint 等待 dailyhot", "dailyhot" in ep)
    check("entrypoint 超时也启动", "anyway" in ep or "继续" in ep)
    check("entrypoint 跑 serve", "main.py serve" in ep)
if DOCKERIGNORE.exists():
    ignore = DOCKERIGNORE.read_text(encoding="utf-8")
    check(".dockerignore 排除 .env", ".env" in ignore)
    check(".dockerignore 排除 data", "data" in ignore)

compose = yaml.safe_load(COMPOSE_YML.read_text(encoding="utf-8")) or {}
services = compose.get("services") or {}
check(
    "四件套服务",
    set(services) >= {"fishnet", "dailyhot", "rsshub", "redis"},
    str(sorted(services)),
)
fish = services.get("fishnet") or {}
check("fishnet build 当前目录", fish.get("build") == ".", str(fish.get("build")))
vols = [str(v) for v in (fish.get("volumes") or [])]
check("挂 data", any("data" in v for v in vols), str(vols))
check("挂 config", any("config" in v for v in vols), str(vols))
env = fish.get("environment") or {}
if isinstance(env, list):
    env = dict(x.split("=", 1) for x in env if isinstance(x, str) and "=" in x)
check(
    "容器内 DailyHot 走服务名",
    str(env.get("FISHNET_DAILYHOT_URL", "")).startswith("http://dailyhot"),
    str(env),
)
check(
    "容器内 RSSHub 走服务名",
    str(env.get("FISHNET_RSSHUB_URL", "")).startswith("http://rsshub"),
    str(env),
)
deps = fish.get("depends_on") or []
if isinstance(deps, dict):
    deps = list(deps)
check("depends_on rsshub+dailyhot", "rsshub" in deps and "dailyhot" in deps, str(deps))
check("restart unless-stopped", fish.get("restart") == "unless-stopped")
dh = services.get("dailyhot") or {}
check("dailyhot 官方镜像", "dailyhot-api" in str(dh.get("image", "")))
rss = services.get("rsshub") or {}
check("rsshub chromium-bundled", "rsshub" in str(rss.get("image", "")))

wewe_overlay = yaml.safe_load(
    (ROOT / "docker-compose.wewe-rss.yml").read_text(encoding="utf-8")
) or {}
wewe_fish = (wewe_overlay.get("services") or {}).get("fishnet") or {}
wewe_env = wewe_fish.get("environment") or {}
if isinstance(wewe_env, list):
    wewe_env = dict(x.split("=", 1) for x in wewe_env if isinstance(x, str) and "=" in x)
check(
    "wewe overlay 注入 FISHNET_WEWE_URL",
    "wewe-rss" in str(wewe_env.get("FISHNET_WEWE_URL", "")),
    str(wewe_env),
)
check(
    "wewe overlay 不覆盖 depends_on",
    "depends_on" not in wewe_fish,
    str(wewe_fish.keys()),
)


print("\n[Lab 9.2] 容器 URL 覆盖")
keys = ("FISHNET_DAILYHOT_URL", "FISHNET_RSSHUB_URL", "FISHNET_WEWE_URL")
saved = {k: os.environ.pop(k, None) for k in keys}
try:
    check("空 env 回落 toml", url_from_env("FISHNET_DAILYHOT_URL", "http://127.0.0.1:6688") == "http://127.0.0.1:6688")
    os.environ["FISHNET_DAILYHOT_URL"] = "http://dailyhot:6688/"
    os.environ["FISHNET_RSSHUB_URL"] = "http://rsshub:1200"
    os.environ["FISHNET_WEWE_URL"] = "http://wewe-rss:4000"
    s = load_settings()
    check("FISHNET_DAILYHOT_URL 覆盖", s.dailyhot_url == "http://dailyhot:6688", s.dailyhot_url)
    check("FISHNET_RSSHUB_URL 覆盖", s.rsshub_url == "http://rsshub:1200", s.rsshub_url)
    check("FISHNET_WEWE_URL 覆盖", s.wewe_url == "http://wewe-rss:4000", s.wewe_url)
    check(
        "本机 wewe 不改写",
        rewrite_wewe_loopback("http://127.0.0.1:4000/feeds/x.atom", "http://127.0.0.1:4000")
        == "http://127.0.0.1:4000/feeds/x.atom",
    )
    check(
        "容器 wewe 改写 loopback",
        rewrite_wewe_loopback("http://127.0.0.1:4000/feeds/x.atom", "http://wewe-rss:4000")
        == "http://wewe-rss:4000/feeds/x.atom",
    )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "sources.yaml"
        wechat = Path(tmp) / "wechat.yaml"
        overlay = Path(tmp) / "overlay.yaml"
        src.write_text("feeds: []\n", encoding="utf-8")
        overlay.write_text("feeds: []\nreplacements: []\ndisabled: []\n", encoding="utf-8")
        wechat.write_text(
            "feeds:\n"
            "  - name: 示例公众号\n"
            "    url: http://127.0.0.1:4000/feeds/MP_WXS_1.atom\n"
            "    source: wechat_mp\n"
            "    kind: article\n"
            "  - name: 占位公众号\n"
            "    url: \"{wewe}/feeds/MP_WXS_2.atom\"\n"
            "    source: wechat_mp\n"
            "    kind: article\n",
            encoding="utf-8",
        )
        feeds = load_feeds(
            src,
            rsshub_url="http://rsshub:1200",
            wewe_url="http://wewe-rss:4000",
            overlay_path=overlay,
            wechat_path=wechat,
        )
        urls = [f["url"] for f in feeds]
        check(
            "wechat loopback 改写成服务名",
            "http://wewe-rss:4000/feeds/MP_WXS_1.atom" in urls,
            str(urls),
        )
        check("{wewe} 占位展开", "http://wewe-rss:4000/feeds/MP_WXS_2.atom" in urls, str(urls))
        check("不再指向 127.0.0.1:4000", all("127.0.0.1:4000" not in u for u in urls), str(urls))
finally:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
