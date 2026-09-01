"""把一期目录转成移动端 edition.json。

articles.json 是 A3 打印契约。客户端另吃一套扁平字段:
section / summary / source_name / original_url / reading_minutes。
热榜在打印端是一整页目录,客户端拆成单条卡片。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from render.edition_to_articles import edition_to_articles, _norm_title
from render.parse_edition import EditionMeta, parse_edition_dir

log = logging.getLogger("fishnet.client")

BRAND = "自动日报"
PERIOD_LABEL = {"am": "早报", "pm": "晚报"}
CHARS_PER_MINUTE = 400
SUMMARY_CHARS = 120
SOURCE_LABELS = {
    "weibo": "微博",
    "zhihu": "知乎",
    "bilibili": "B站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat_mp": "公众号",
    "toutiao": "头条",
    "thepaper": "澎湃",
    "news": "新闻",
    "finance": "财经",
    "rss": "RSS",
    "other": "其它",
}
SOURCE_TYPES = set(SOURCE_LABELS)

_H1 = re.compile(r"^# (.+)$", re.M)
_URL = re.compile(r"https?://[^\s`<>]+")
_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)$")
_DATELINE = re.compile(r"^(今天|昨天|\d{4}年)")
_HOT_ITEM = re.compile(
    r"^(\d+)\.\s+\*\*(.+?)\*\*(?:\s*·\s*([^\s·]+))?(?:\s*·\s*(.+))?$"
)


def edition_to_client(
    edition_dir: Path,
    *,
    kind: str | None = None,
    articles: list[dict[str, Any]] | None = None,
    meta: EditionMeta | None = None,
) -> dict[str, Any]:
    edition_dir = Path(edition_dir)
    if articles is None or meta is None:
        articles, meta = edition_to_articles(edition_dir, kind=kind)
    kind = kind or meta.kind or ("pm" if edition_dir.name.endswith("-pm") else "am")
    eid = meta.edition_id or edition_dir.name
    date, period = parse_edition_id(eid)
    lookup = _meta_lookup(edition_dir)

    out_articles: list[dict[str, Any]] = []
    for art in articles:
        section = (art.get("metadata") or {}).get("section") or art.get("section") or ""
        if section == "hotlist":
            split = _split_hotlist(art)
            if split:
                out_articles.extend(split)
                continue
        out_articles.append(_client_article(art, lookup))

    return {
        "id": eid,
        "date": date,
        "period": period,
        "period_label": PERIOD_LABEL.get(period, "早报"),
        "brand": BRAND,
        "articles": out_articles,
    }


def write_client_edition(
    edition_dir: Path,
    *,
    kind: str | None = None,
    articles: list[dict[str, Any]] | None = None,
    meta: EditionMeta | None = None,
    path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    edition_dir = Path(edition_dir)
    payload = edition_to_client(
        edition_dir, kind=kind, articles=articles, meta=meta
    )
    dest = path or (edition_dir / "edition.json")
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_client_index(edition_dir.parent)
    return dest, payload


def write_client_index(editions_dir: Path) -> dict[str, Any]:
    editions_dir = Path(editions_dir)
    rows: list[dict[str, str]] = []
    if editions_dir.is_dir():
        for path in sorted(editions_dir.iterdir(), reverse=True):
            if not path.is_dir() or path.name.startswith("_"):
                continue
            if not (path / "edition.json").exists():
                continue
            date, period = parse_edition_id(path.name)
            rows.append(
                {
                    "id": path.name,
                    "date": date,
                    "period": period,
                    "period_label": PERIOD_LABEL.get(period, "早报"),
                }
            )
    index = {"latest": rows[0]["id"] if rows else "", "editions": rows}
    if editions_dir.is_dir():
        (editions_dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return index


def export_all_client_editions(editions_dir: Path) -> dict[str, Any]:
    editions_dir = Path(editions_dir)
    if not editions_dir.is_dir():
        return write_client_index(editions_dir)
    for path in sorted(editions_dir.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if not _looks_like_edition(path):
            continue
        try:
            write_client_edition(path)
        except Exception as e:
            log.warning("skip %s: %s", path.name, e)
    return write_client_index(editions_dir)


def parse_edition_id(eid: str) -> tuple[str, str]:
    m = _ID_RE.match(eid)
    if m:
        return m.group(1), m.group(2)
    if eid.endswith("-pm"):
        return eid[: -len("-pm")], "pm"
    if eid.endswith("-am"):
        return eid[: -len("-am")], "am"
    return eid, "am"


def _looks_like_edition(path: Path) -> bool:
    return (
        (path / "01_headline.md").exists()
        or (path / "digest.md").exists()
        or (path / "articles.json").exists()
    )


def _client_article(art: dict[str, Any], lookup: dict[str, dict[str, str]]) -> dict[str, Any]:
    meta = art.get("metadata") or {}
    section = meta.get("section") or art.get("section") or ""
    markdown = _trim_boilerplate(art.get("markdown") or "")
    extra = _lookup_meta(art.get("title") or "", lookup)
    return {
        "id": art.get("id") or "",
        "title": art.get("title") or "",
        "markdown": markdown,
        "summary": _summary(markdown),
        "section": section,
        "source_name": extra.get("source_name")
        or SOURCE_LABELS.get(extra.get("source_type", ""), ""),
        "source_type": extra.get("source_type", ""),
        "published_label": extra.get("published_label", ""),
        "original_url": extra.get("original_url", ""),
        "reading_minutes": _reading_minutes(markdown),
        "images": art.get("images") or [],
        "priority": art.get("priority", 0.5),
        "kind": art.get("kind") or "normal",
    }


def _lookup_meta(title: str, lookup: dict[str, dict[str, str]]) -> dict[str, str]:
    key = _norm_title(title)
    if key in lookup:
        return lookup[key]
    for other, meta in lookup.items():
        if key and (key in other or other in key):
            return meta
    return {}


def _meta_lookup(edition_dir: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    items_dir = edition_dir / "items"
    if items_dir.is_dir():
        for path in items_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            title = _first_h1(text)
            if not title:
                continue
            out[_norm_title(title)] = _parse_item_text(text)
    parsed, _meta = parse_edition_dir(edition_dir)
    for art in parsed:
        key = _norm_title(art.title)
        row = out.setdefault(key, {})
        if art.url and not row.get("original_url"):
            row["original_url"] = art.url
        if art.byline:
            parsed_byline = _parse_byline(art.byline)
            for k, v in parsed_byline.items():
                if v and not row.get(k):
                    row[k] = v
    return out


def _parse_item_text(text: str) -> dict[str, str]:
    url = ""
    byline = ""
    seen_title = False
    for line in text.splitlines():
        s = line.strip()
        if not seen_title and s.startswith("# "):
            seen_title = True
            continue
        if s.startswith(">") and not byline:
            byline = s.lstrip("> ").strip()
            continue
        if s.startswith("原文地址"):
            m = _URL.search(s)
            if m:
                url = m.group(0).rstrip(").,，。")
    row = _parse_byline(byline)
    if url:
        row["original_url"] = url
    return row


def _parse_byline(line: str) -> dict[str, str]:
    parts = [p.strip() for p in (line or "").split("·") if p.strip()]
    published = ""
    source_type = ""
    names: list[str] = []
    for part in parts:
        low = part.casefold()
        if not published and _DATELINE.match(part):
            published = part
            continue
        if not source_type and low in SOURCE_TYPES:
            source_type = low
            continue
        if part in {"总分"} or part.startswith("sim ") or "挑战了" in part:
            continue
        names.append(part)
    return {
        "published_label": published,
        "source_type": source_type,
        "source_name": " ".join(names).strip(),
    }


def _split_hotlist(art: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = _parse_hotlist_markdown(art.get("markdown") or "")
    if len(rows) < 2:
        return None
    base_id = art.get("id") or "hot"
    base_priority = float(art.get("priority") or 0.28)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        body = row["summary"] or row["title"]
        out.append(
            {
                "id": f"{base_id}-{i:02d}",
                "title": row["title"],
                "markdown": body,
                "summary": _summary(body),
                "section": "hotlist",
                "source_name": row["source_name"],
                "source_type": row["source_type"],
                "published_label": row["published_label"],
                "original_url": "",
                "reading_minutes": 1,
                "images": [],
                "priority": round(base_priority - i * 0.001, 4),
                "kind": "brief",
            }
        )
    return out


def _parse_hotlist_markdown(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    blurbs: list[str] = []

    def flush() -> None:
        nonlocal current, blurbs
        if current is None:
            return
        current["summary"] = " ".join(blurbs).strip()
        rows.append(current)
        current = None
        blurbs = []

    for raw in markdown.splitlines():
        line = raw.strip()
        m = _HOT_ITEM.match(line)
        if m:
            flush()
            rest = (m.group(4) or "").strip()
            source = (m.group(3) or "").strip()
            published = ""
            for part in [p.strip() for p in rest.split("·") if p.strip()]:
                if not published and _DATELINE.match(part):
                    published = part
            source_type = source.casefold() if source.casefold() in SOURCE_TYPES else ""
            current = {
                "title": m.group(2).strip(),
                "source_type": source_type,
                "source_name": SOURCE_LABELS.get(source_type, source),
                "published_label": published,
                "summary": "",
            }
            continue
        if current is not None and line and not line.startswith("#"):
            blurbs.append(line)
    flush()
    return rows


def _trim_boilerplate(markdown: str) -> str:
    lines: list[str] = []
    for line in (markdown or "").splitlines():
        s = line.strip()
        if s.startswith("风险提示") or s.startswith("以上精彩内容来自") or "年度会员" in s:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _summary(markdown: str) -> str:
    text = re.sub(r"[#>*`]", "", markdown or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= SUMMARY_CHARS:
        return text
    return text[: SUMMARY_CHARS - 1].rstrip() + "…"


def _reading_minutes(markdown: str) -> int:
    n = len((markdown or "").replace(" ", ""))
    return max(1, round(n / CHARS_PER_MINUTE))


def _first_h1(text: str) -> str:
    m = _H1.search(text)
    return m.group(1).strip() if m else ""
