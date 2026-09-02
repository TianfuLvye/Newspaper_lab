"""把一期报纸收成邮件可读的摘要,而不是把 A3 digest.html 塞进正文。

A3 报纸 HTML 约数 MB、依赖打印 CSS 和内嵌图,邮箱会裁样式或拒收。
正文用头版/深度标题列表;PDF 当附件归档。
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_LABELS = {
    "lede": "今日综述",
    "headline": "头版",
    "deepread": "深度阅读",
    "oral": "口播",
    "critical": "今日一问",
    "subscribe": "订阅",
    "hotlist": "热榜",
    "health": "系统体检",
}

# 邮件只扫读。体检默认不进正文(报纸末页已经有)。
SECTION_LIMITS = {
    "lede": 1,
    "headline": 6,
    "deepread": 8,
    "oral": 2,
    "critical": 3,
    "subscribe": 6,
    "hotlist": 1,
    "health": 0,
}

SECTION_ORDER = [
    "lede",
    "headline",
    "deepread",
    "oral",
    "critical",
    "subscribe",
    "hotlist",
]


@dataclass
class Story:
    title: str
    summary: str
    section: str
    url: str = ""


@dataclass
class DigestMail:
    edition_id: str
    kind: str
    subject: str
    text: str
    html: str
    stories: list[Story] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)


def edition_kind(edition_id: str) -> str:
    if edition_id.endswith("-pm"):
        return "pm"
    if edition_id.endswith("-am"):
        return "am"
    return "am"


def period_label(kind: str) -> str:
    return "晚报" if kind == "pm" else "早报"


def date_label(edition_id: str) -> str:
    if edition_id[-3:] in {"-am", "-pm"}:
        return edition_id[:-3]
    return edition_id


def _first_paragraph(markdown: str, *, limit: int = 140) -> str:
    chunks: list[str] = []
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line:
            if chunks:
                break
            continue
        if line.startswith(("#", ">", "![")):
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("**", "").replace("*", "")
        chunks.append(line)
        if sum(len(c) for c in chunks) >= limit:
            break
    text = " ".join(chunks)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _load_articles(edition_dir: Path) -> list[dict]:
    path = edition_dir / "articles.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("articles") or []
    return [row for row in data if isinstance(row, dict)]


def stories_from_articles(articles: list[dict]) -> list[Story]:
    grouped: dict[str, list[Story]] = {}
    for row in articles:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        section = str(meta.get("section") or row.get("section") or "")
        limit = SECTION_LIMITS.get(section)
        if not limit:
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        bucket = grouped.setdefault(section, [])
        if len(bucket) >= limit:
            continue
        md = str(row.get("markdown") or row.get("summary") or "")
        url = str(row.get("url") or meta.get("url") or "")
        bucket.append(
            Story(
                title=title,
                summary=_first_paragraph(md),
                section=section,
                url=url,
            )
        )
    out: list[Story] = []
    for section in SECTION_ORDER:
        out.extend(grouped.get(section, []))
    return out


def _fallback_stories(edition_dir: Path) -> list[Story]:
    digest = edition_dir / "digest.md"
    if not digest.is_file():
        return []
    stories: list[Story] = []
    for raw in digest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            title = re.sub(r"^##\s+(F\d+\s*·\s*)?", "", line).strip()
            if title:
                stories.append(Story(title=title, summary="", section="headline"))
        if len(stories) >= 12:
            break
    return stories


def pick_attachments(
    edition_dir: Path,
    *,
    attach_pdf: bool = True,
    attach_html: bool = False,
    max_bytes: int = 15 * 1024 * 1024,
) -> list[Path]:
    files: list[Path] = []
    if attach_pdf:
        pdf = edition_dir / "digest.pdf"
        if pdf.is_file() and pdf.stat().st_size > 0:
            files.append(pdf)
    if attach_html:
        html_path = edition_dir / "digest.html"
        if html_path.is_file() and html_path.stat().st_size > 0:
            files.append(html_path)
    kept: list[Path] = []
    total = 0
    for path in files:
        size = path.stat().st_size
        if total + size > max_bytes:
            continue
        kept.append(path)
        total += size
    return kept


def _text_body(edition_id: str, kind: str, stories: list[Story], attachments: list[Path]) -> str:
    label = period_label(kind)
    date = date_label(edition_id)
    lines = [f"自动日报 · {label} · {date}", ""]
    current = None
    for story in stories:
        if story.section != current:
            current = story.section
            lines.append(SECTION_LABELS.get(current, current))
            lines.append("")
        lines.append(f"- {story.title}")
        if story.summary:
            lines.append(f"  {story.summary}")
        lines.append("")
    if attachments:
        names = ", ".join(p.name for p in attachments)
        lines.append(f"附件: {names}")
    else:
        lines.append("本期没有可附的 PDF(排版失败时仍可看上面的目录)。")
    lines.append("")
    lines.append("本邮件由 fishnet 自动发送,仅供个人阅读。")
    return "\n".join(lines).strip() + "\n"


def _html_body(edition_id: str, kind: str, stories: list[Story], attachments: list[Path]) -> str:
    label = period_label(kind)
    date = date_label(edition_id)
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>自动日报 · {html.escape(label)} · {html.escape(date)}</title>",
        "</head>",
        (
            '<body style="font-family:Georgia,\'Songti SC\',serif;max-width:640px;'
            'margin:0 auto;padding:24px;color:#111;line-height:1.45">'
        ),
        f"<h1 style=\"font-size:22px;margin:0 0 4px\">自动日报 · {html.escape(label)}</h1>",
        f'<p style="color:#666;margin:0 0 20px">{html.escape(date)} · {html.escape(edition_id)}</p>',
    ]
    current = None
    for story in stories:
        if story.section != current:
            current = story.section
            heading = SECTION_LABELS.get(current, current)
            parts.append(
                f'<h2 style="font-size:16px;border-bottom:1px solid #111;'
                f'padding-bottom:4px;margin:22px 0 10px">{html.escape(heading)}</h2>'
            )
        title = html.escape(story.title)
        if story.url:
            title = f'<a href="{html.escape(story.url, quote=True)}">{title}</a>'
        parts.append(f'<p style="margin:0 0 12px"><b>{title}</b>')
        if story.summary:
            parts.append(
                f'<br><span style="color:#444;font-size:14px">'
                f"{html.escape(story.summary)}</span>"
            )
        parts.append("</p>")
    if attachments:
        names = "、".join(html.escape(p.name) for p in attachments)
        parts.append(f'<p style="color:#666;margin-top:24px">完整报纸见附件：{names}</p>')
    else:
        parts.append('<p style="color:#666;margin-top:24px">本期没有 PDF 附件。</p>')
    parts.append('<p style="color:#999;font-size:12px">fishnet 自动发送 · 仅供个人阅读</p>')
    parts.append("</body></html>")
    return "\n".join(parts)


def compose_digest(
    edition_dir: Path,
    *,
    attach_pdf: bool = True,
    attach_html: bool = False,
    max_attach_bytes: int = 15 * 1024 * 1024,
) -> DigestMail:
    dest = Path(edition_dir)
    eid = dest.name
    kind = edition_kind(eid)
    articles = _load_articles(dest)
    stories = stories_from_articles(articles) if articles else _fallback_stories(dest)
    attachments = pick_attachments(
        dest,
        attach_pdf=attach_pdf,
        attach_html=attach_html,
        max_bytes=max_attach_bytes,
    )
    subject = f"自动日报 · {period_label(kind)} · {date_label(eid)}"
    return DigestMail(
        edition_id=eid,
        kind=kind,
        subject=subject,
        text=_text_body(eid, kind, stories, attachments),
        html=_html_body(eid, kind, stories, attachments),
        stories=stories,
        attachments=attachments,
    )
