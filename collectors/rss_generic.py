"""通用 RSS Collector —— Lab 3。

一张网适配 N 个 feed:配置在 config/sources.yaml 的 feeds 段。
部分源会在 content:encoded 里直接给全文,先捡白嫖再决定要不要走 Lab 5 抽取。
"""
from __future__ import annotations

import re
import time
from calendar import timegm
from datetime import datetime, timezone
from html import unescape
from typing import Any

import feedparser

from core.base import BaseCollector
from core.schema import Item, Kind, Source

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def slugify(name: str) -> str:
    """把订阅显示名收成 collector 后缀:ascii/中文都可,空白变下划线。"""
    s = name.strip().lower()
    s = re.sub(r"[\s/\\]+", "_", s)
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s, flags=re.UNICODE)
    return s or "unnamed"


def _strip_html(html: str) -> str:
    text = unescape(_TAG_RE.sub(" ", html or ""))
    return _WS_RE.sub(" ", text).strip()


def _extract_content(entry: dict) -> str | None:
    """优先 content:encoded / content[],再退到 summary。返回纯文本或 None。"""
    for c in entry.get("content") or []:
        val = c.get("value") if isinstance(c, dict) else None
        if val and len(_strip_html(val)) > 80:
            return _strip_html(val)
    encoded = entry.get("content_encoded") or entry.get("content:encoded")
    if encoded and len(_strip_html(str(encoded))) > 80:
        return _strip_html(str(encoded))
    return None


def _parse_struct_time(st) -> datetime | None:
    if st is None:
        return None
    try:
        return datetime.fromtimestamp(timegm(st), tz=timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _entry_to_raw(entry: Any) -> dict:
    """feedparser 的 entry 不是纯 dict,尽量收成可 JSON 化的结构。"""
    if isinstance(entry, dict):
        keys = entry.keys()
    else:
        keys = getattr(entry, "keys", lambda: [])()
    out: dict = {}
    for k in keys:
        try:
            v = entry.get(k) if hasattr(entry, "get") else entry[k]
        except Exception:
            continue
        if k in ("published_parsed", "updated_parsed", "created_parsed"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [
                {kk: vv for kk, vv in (x.items() if hasattr(x, "items") else [])}
                if not isinstance(x, (str, int, float, bool, type(None)))
                else x
                for x in v[:20]
            ]
        else:
            out[k] = str(v)[:2000]
    return out


class RSSCollector(BaseCollector):
    interval_minutes = 60

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = f"rss_{slugify(cfg['name'])}"
        # 允许单源覆盖默认轮询间隔(高频 UP 可配更短)
        if cfg.get("interval_minutes"):
            self.interval_minutes = int(cfg["interval_minutes"])

    def collect(self):
        url = self.cfg["url"]
        feed = None
        last_exc: Exception | None = None
        # RSSHub 偶发返回 HTML 错误页;轻量重试一次
        for attempt in range(2):
            feed = feedparser.parse(url)
            if feed.entries:
                break
            if getattr(feed, "bozo", 0):
                last_exc = RuntimeError(f"feed broken: {feed.bozo_exception}")
            else:
                last_exc = RuntimeError(f"feed empty: {url}")
            if attempt == 0:
                time.sleep(1.5)
        assert feed is not None
        # bozo=1 但有 entries 是常态(不规范 XML);只有「坏且空」才算失败
        if not feed.entries:
            raise last_exc or RuntimeError(f"feed empty: {url}")

        source = Source(self.cfg["source"])
        kind = Kind(self.cfg.get("kind", "article"))
        fallback_author = self.cfg["name"]

        for e in feed.entries:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title and not link:
                continue
            summary = _strip_html(e.get("summary", "") or e.get("description", ""))[:500]
            content = _extract_content(e)
            tags = []
            for t in e.get("tags") or []:
                term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
                if term:
                    tags.append(str(term))
            published = _parse_struct_time(
                e.get("published_parsed") or e.get("updated_parsed")
            )
            yield Item(
                source=source,
                kind=kind,
                title=title or link,
                url=link or f"urn:rss:{self.name}:{title}",
                summary=summary or None,
                content=content,
                author=e.get("author") or fallback_author,
                published_at=published,
                collector=self.name,
                tags=tags,
                raw=_entry_to_raw(e),
            )
