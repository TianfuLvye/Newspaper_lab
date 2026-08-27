"""入库 / 出报共用的文本清洗:保留段落、裁知乎尾巴、补全日报标题。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape

_BLOCK_BREAK = re.compile(
    r"(?is)"
    r"<\s*br\s*/?\s*>|"
    r"<\s*/\s*(?:p|div|h[1-6]|li|tr|blockquote|section|article|ul|ol|table)\s*>"
)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[^\S\n]+")
_BLANK = re.compile(r"\n{3,}")
_ZHIHU_SKIP_TITLE = re.compile(
    r"赞同了(?:回答|想法|文章)?|关注了|喜欢了"
)
_DAILY_HEAD = re.compile(r"\d+[、.]\s*(.+?[？?])")
_AUTHOR_NEAR_RELATED = re.compile(
    r"author\s*[:：]?.{0,160}相关文章",
    re.I | re.S,
)


def html_to_text(html: str, *, single_line: bool = False) -> str:
    """把 HTML 收成纯文本。块级标签变成段落空行,不把全文压成一行。"""
    if not html:
        return ""
    text = unescape(html).replace("\xa0", " ")
    text = _BLOCK_BREAK.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    return normalize_paragraphs(text, single_line=single_line)


def normalize_paragraphs(text: str, *, single_line: bool = False) -> str:
    """行内空白收拢,段落之间保留一个空行。"""
    text = (text or "").replace("\xa0", " ")
    text = _INLINE_WS.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK.sub("\n\n", text).strip()
    if single_line:
        return re.sub(r"\s+", " ", text).strip()
    return text


def strip_zhihu_footer(text: str) -> str:
    """知乎 RSS 文末 author + 相关文章 是推荐栏,从 author 起整段丢掉。"""
    if not text:
        return text
    window = text[-1500:] if len(text) > 1500 else text
    m = _AUTHOR_NEAR_RELATED.search(window)
    if not m:
        return text.strip()
    abs_start = (len(text) - len(window)) + m.start()
    return text[:abs_start].rstrip()


def is_zhihu_skip_title(title: str) -> bool:
    """动态时间线上的赞同/关注,不是作者自己写的回答。"""
    return bool(_ZHIHU_SKIP_TITLE.search(title or ""))


def is_zhihu_activity_item(it) -> bool:
    source = getattr(it, "source", None)
    src = source.value if hasattr(source, "value") else source
    if src != "zhihu":
        return False
    if is_zhihu_skip_title(getattr(it, "title", "") or ""):
        return True
    collector = getattr(it, "collector", "") or ""
    return collector.endswith("_动态")


def expand_zhihu_daily_title(title: str, body: str) -> str:
    """RSSHub 会把早报标题截成「…」。用正文里的热点条目拼回完整目录。"""
    cut = (body or "").find("小李精选")
    region = body[:cut] if cut >= 0 else (body or "")
    heads: list[str] = []
    for m in _DAILY_HEAD.finditer(region):
        h = m.group(1).strip()
        if "小李精选" in h or h.startswith("背景"):
            continue
        h = re.split(r"\s*背景", h, maxsplit=1)[0].strip()
        if len(h) < 8:
            continue
        heads.append(h)
        if len(heads) >= 8:
            break
    if len(heads) >= 2:
        return "；".join(heads)
    return title


def readable_body(it) -> str:
    """报纸/打分用的正文。视频没有转写就留空,知乎裁掉文末推荐。"""
    kind = getattr(it, "kind", None)
    kind_v = kind.value if hasattr(kind, "value") else kind
    if kind_v == "video":
        return ""
    raw = (getattr(it, "content", None) or getattr(it, "summary", None) or "")
    text = normalize_paragraphs(raw)
    source = getattr(it, "source", None)
    src = source.value if hasattr(source, "value") else source
    if src == "zhihu":
        text = strip_zhihu_footer(text)
    return text.strip()


def display_title(it) -> str:
    """展示用标题:剥 HTML,日报截断则用正文条目补全。"""
    title = html_to_text(getattr(it, "title", "") or "", single_line=True)
    collector = getattr(it, "collector", "") or ""
    source = getattr(it, "source", None)
    src = source.value if hasattr(source, "value") else source
    if "知乎日报" in collector or (src == "zhihu" and "早报" in title):
        title = expand_zhihu_daily_title(title, readable_body(it))
    return title


def item_published_at(it):
    """出报窗口看刊出时间;没有 published_at 才退到抓取时间。"""
    return getattr(it, "published_at", None) or getattr(it, "fetched_at", None)


CST = timezone(timedelta(hours=8))


def format_dateline(when, *, now: datetime | None = None) -> str:
    """报纸题下的刊出时间:今天/昨天写到分钟,更早只写年月日。"""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=CST)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CST)
    local = when.astimezone(CST)
    today = now.astimezone(CST).date()
    day = local.date()
    hm = local.strftime("%H:%M")
    if day == today:
        return f"今天 {hm}"
    if day == today - timedelta(days=1):
        return f"昨天 {hm}"
    return f"{day.year}年{day.month}月{day.day}日"
