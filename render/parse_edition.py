"""从一期目录的 Markdown 版面文件读出 Article。不回头打 collector。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from render.layout.model import Article, ImageSpec

_SECTION_FILES: list[tuple[str, str, str, str]] = [
    ("01_headline.md", "headline", "头版", "story"),
    ("02_hotlist.md", "hotlist", "热榜", "index"),
    ("03_deepread.md", "deepread", "深度", "story"),
    ("07_critical.md", "critical", "今日一问", "story"),
    ("06_subscribe.md", "subscribe", "订阅", "story"),
    ("99_health.md", "health", "体检", "index"),
]

_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HTML_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', re.I)
_H1 = re.compile(r"^# (.+)$", re.M)
_FN = re.compile(r"^F(\d+)\s*·\s*(.+)$")

_MAX_CHARS = {
    "headline": 2400,
    "deepread": 3600,
    "critical": 2200,
    "subscribe": 1400,
    "hotlist": 8000,
    "health": 6000,
}
_MAX_PAGES = {
    "headline": 2,
    "deepread": 3,
    "critical": 2,
    "subscribe": 2,
    "hotlist": 2,
    "health": 2,
}


@dataclass
class EditionMeta:
    edition_id: str
    kind: str
    generated: str = ""


def parse_edition_dir(edition_dir: Path) -> tuple[list[Article], EditionMeta]:
    edition_dir = Path(edition_dir)
    meta = _meta_from_digest(edition_dir)
    articles: list[Article] = []
    for fname, section, kicker, role in _SECTION_FILES:
        path = edition_dir / fname
        if not path.exists():
            articles.append(
                Article(
                    id=f"{section}-missing",
                    section=section,
                    role="placeholder",
                    title=f"{kicker}暂缺",
                    kicker=kicker,
                    body="本栏目今日无数据。",
                    empty=True,
                    priority=90,
                    max_chars=200,
                    max_pages=1,
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        articles.extend(_parse_section(text, section, kicker, role))
    if not any(a.section == "headline" for a in articles):
        # 只有 digest.md 的退化路径
        digest = edition_dir / "digest.md"
        if digest.exists():
            articles = _parse_digest_fallback(digest.read_text(encoding="utf-8"))
            meta = _meta_from_text(digest.read_text(encoding="utf-8"), fallback=edition_dir.name)
    return articles, meta


def _meta_from_digest(edition_dir: Path) -> EditionMeta:
    digest = edition_dir / "digest.md"
    name = edition_dir.name
    kind = "pm" if name.endswith("-pm") else "am"
    if digest.exists():
        return _meta_from_text(digest.read_text(encoding="utf-8"), fallback=name)
    return EditionMeta(edition_id=name, kind=kind)


def _meta_from_text(text: str, *, fallback: str) -> EditionMeta:
    kind = "pm" if "晚报" in text[:80] or fallback.endswith("-pm") else "am"
    eid = fallback
    m = re.search(r"期号 `([^`]+)`", text)
    if m:
        eid = m.group(1)
    gen = ""
    g = re.search(r"生成于 ([^\n]+)", text)
    if g:
        gen = g.group(1).strip()
    return EditionMeta(edition_id=eid, kind=kind, generated=gen)


def _parse_section(text: str, section: str, kicker: str, role: str) -> list[Article]:
    if "本栏目今日无数据" in text[:400] or "本栏目今日无入选" in text[:400]:
        return [
            Article(
                id=f"{section}-empty",
                section=section,
                role="placeholder",
                title=f"{kicker}今日无稿",
                kicker=kicker,
                body="本栏目今日无数据。",
                empty=True,
                priority=80,
                max_chars=200,
                max_pages=1,
            )
        ]
    if role == "index":
        body = _clean_index_body(text)
        title = _first_h1(text) or kicker
        return [
            Article(
                id=f"{section}-index",
                section=section,
                role="index",
                title=title,
                kicker=kicker,
                body=body,
                priority=20 if section == "hotlist" else 70,
                max_chars=_MAX_CHARS[section],
                max_pages=_MAX_PAGES[section],
            )
        ]

    parts = _split_h2(text)
    if section == "subscribe":
        parts = [p for p in parts if p[0] not in ("订阅清单", "目录")]
        toc = _subscribe_toc(text)
        out: list[Article] = []
        if toc:
            out.append(
                Article(
                    id="subscribe-toc",
                    section=section,
                    role="index",
                    title="订阅目录",
                    kicker=kicker,
                    body=toc,
                    priority=40,
                    max_chars=1200,
                    max_pages=1,
                )
            )
        for i, (title, body) in enumerate(parts):
            if "已上头版" in body or "此处不重复全文" in body:
                continue
            out.append(_story(section, kicker, title, body, i))
        if not parts:
            out.append(
                Article(
                    id="subscribe-empty",
                    section=section,
                    role="placeholder",
                    title="订阅今日无稿",
                    kicker=kicker,
                    body="本栏目今日无数据。",
                    empty=True,
                    max_chars=200,
                    max_pages=1,
                )
            )
        return out

    stories = []
    for i, (title, body) in enumerate(parts):
        stories.append(_story(section, kicker, title, body, i))
    if not stories:
        return [
            Article(
                id=f"{section}-empty",
                section=section,
                role="placeholder",
                title=f"{kicker}今日无稿",
                kicker=kicker,
                body="本栏目今日无数据。",
                empty=True,
                max_chars=200,
                max_pages=1,
            )
        ]
    return stories


def _story(section: str, kicker: str, title: str, body: str, idx: int) -> Article:
    fn = ""
    m = _FN.match(title.strip())
    if m:
        fn = f"F{int(m.group(1)):02d}"
        title = m.group(2).strip()
    body, images, url, byline = _clean_story_body(body)
    return Article(
        id=fn or f"{section}-{idx+1:02d}",
        section=section,
        role="story",
        title=title.strip() or kicker,
        kicker=kicker,
        byline=byline,
        body=body,
        url=url,
        images=images,
        fn=fn,
        priority=idx,
        max_chars=_MAX_CHARS.get(section, 2000),
        max_pages=_MAX_PAGES.get(section, 2),
        empty=not body.strip(),
    )


def _split_h2(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    chunks: list[tuple[str, str]] = []
    title: str | None = None
    buf: list[str] = []
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if title is not None:
                chunks.append((title, "\n".join(buf).strip()))
            title = line[3:].strip()
            buf = []
        else:
            if title is not None:
                buf.append(line)
    if title is not None:
        chunks.append((title, "\n".join(buf).strip()))
    return chunks


def _first_h1(text: str) -> str:
    m = _H1.search(text)
    return m.group(1).strip() if m else ""


def _clean_index_body(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if s.startswith(">"):
            continue
        if s.startswith("读完再打点"):
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _subscribe_toc(text: str) -> str:
    m = re.search(r"## 目录\n+(.*?)(?:\n---|\n## )", text, re.S)
    if not m:
        return ""
    return m.group(1).strip()


def _clean_story_body(text: str) -> tuple[str, list[ImageSpec], str, str]:
    images: list[ImageSpec] = []

    def on_md(m: re.Match) -> str:
        images.append(ImageSpec(src=m.group(2).strip(), alt=m.group(1), caption=m.group(1)))
        return ""

    def on_html(m: re.Match) -> str:
        images.append(ImageSpec(src=m.group(1).strip()))
        return ""

    text = _IMG_RE.sub(on_md, text)
    text = _HTML_IMG_RE.sub(on_html, text)

    url = ""
    byline = ""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">"):
            if "总分" in s or "挑战了" in s:
                continue
            if byline == "" and "·" in s:
                byline = s.lstrip("> ").strip()[:80]
            continue
        if s.startswith("读完再打点"):
            continue
        if s.startswith("原文地址"):
            m = re.search(r"https?://[^\s`]+", s)
            if m:
                url = m.group(0).rstrip(")")
            continue
        if s.startswith("http") and not url:
            url = s.strip("`")
            continue
        if s.startswith("以上精彩内容来自") or "年度会员" in s:
            break
        if s.startswith("风险提示"):
            break
        if s and set(s) <= set("~─—-_= "):
            continue
        out.append(line.rstrip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return body, images[:3], url, byline


def _parse_digest_fallback(text: str) -> list[Article]:
    # 极少用:按 # 二级版面再拆 ##
    articles: list[Article] = []
    return articles
