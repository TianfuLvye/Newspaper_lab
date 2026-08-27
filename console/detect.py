"""把粘贴进来的链接收成一份 Feed 草稿。不写盘、不打网络。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import unquote, urlparse

_ZHIHU_USER = re.compile(
    r"(?:https?://)?(?:www\.)?zhihu\.com/(people|org)/([^/?#]+)",
    re.IGNORECASE,
)
_WECHAT_ARTICLE = re.compile(
    r"(?:https?://)?mp\.weixin\.qq\.com/s[/?][^\s]+",
    re.IGNORECASE,
)
_WEWE_FEED = re.compile(
    r"(?:https?://[^\s/]+)?/feeds?/(MP_WXS_\d+)(?:\.(atom|rss|json))?",
    re.IGNORECASE,
)
_RSSHUB_ZHIHU_ANSWERS = re.compile(
    r"(?:\{rsshub\}|https?://[^\s/]+)?/zhihu/people/answers/([^/?#]+)",
    re.IGNORECASE,
)
_RSSHUB_ZHIHU_ORG = re.compile(
    r"(?:\{rsshub\}|https?://[^\s/]+)?/zhihu/posts/org/([^/?#]+)",
    re.IGNORECASE,
)


class DetectError(ValueError):
    """粘贴内容认不出来。"""


@dataclass
class FeedDraft:
    type: str
    name: str
    url: str
    source: str
    kind: str
    weight: float = 2.0
    title_regex: str | None = None
    interval_minutes: int | None = None
    needs_wewe: bool = False
    wechat_article_url: str | None = None
    mp_id: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_feed_row(self) -> dict:
        row: dict = {
            "name": self.name,
            "url": self.url,
            "source": self.source,
            "kind": self.kind,
            "weight": self.weight,
        }
        if self.title_regex:
            row["title_regex"] = self.title_regex
        if self.interval_minutes:
            row["interval_minutes"] = int(self.interval_minutes)
        return row


def _guess_source_kind(url: str) -> tuple[str, str]:
    u = url.lower()
    if "zhihu.com" in u or "/zhihu/" in u:
        return "zhihu", "article"
    if "mp.weixin.qq.com" in u or "wechat_mp" in u or "MP_WXS_" in url:
        return "wechat_mp", "article"
    if any(x in u for x in ("dj.com", "wsj.com", "wallstreetcn", "finance")):
        return "finance", "article"
    if "bangumi" in u:
        return "bilibili", "video"
    return "rss", "article"


def _pretty_token(token: str) -> str:
    return unquote(token).replace("-", " ").strip() or token


def _normalize_http(raw: str) -> str:
    s = raw.strip()
    if s.startswith("//"):
        return "https:" + s
    if re.match(r"^https?://", s, re.IGNORECASE):
        return s
    if s.startswith(("mp.weixin.qq.com", "www.zhihu.com", "zhihu.com")):
        return "https://" + s
    return s


def detect_input(raw: str, *, wewe_base: str = "http://127.0.0.1:4000") -> FeedDraft:
    """识别知乎主页 / 公众号文章 / WeWe feed / 通用 RSS。"""
    text = (raw or "").strip()
    if not text:
        raise DetectError("请粘贴知乎主页、公众号文章链接，或 RSS 地址")

    m_answers = _RSSHUB_ZHIHU_ANSWERS.search(text)
    if m_answers:
        token = unquote(m_answers.group(1))
        return FeedDraft(
            type="zhihu",
            name=f"{_pretty_token(token)} 回答",
            url=f"{{rsshub}}/zhihu/people/answers/{token}",
            source="zhihu",
            kind="article",
            weight=2.0,
        )

    m_org_route = _RSSHUB_ZHIHU_ORG.search(text)
    if m_org_route:
        token = unquote(m_org_route.group(1))
        return FeedDraft(
            type="zhihu",
            name=_pretty_token(token),
            url=f"{{rsshub}}/zhihu/posts/org/{token}",
            source="zhihu",
            kind="article",
            weight=1.6,
        )

    m_zhihu = _ZHIHU_USER.search(text)
    if m_zhihu:
        kind, token = m_zhihu.group(1).lower(), unquote(m_zhihu.group(2))
        if kind == "org":
            return FeedDraft(
                type="zhihu",
                name=_pretty_token(token),
                url=f"{{rsshub}}/zhihu/posts/org/{token}",
                source="zhihu",
                kind="article",
                weight=1.6,
            )
        return FeedDraft(
            type="zhihu",
            name=f"{_pretty_token(token)} 回答",
            url=f"{{rsshub}}/zhihu/people/answers/{token}",
            source="zhihu",
            kind="article",
            weight=2.0,
        )

    m_wechat = _WECHAT_ARTICLE.search(text)
    if m_wechat:
        article = m_wechat.group(0)
        if not article.lower().startswith("http"):
            article = "https://" + article
        return FeedDraft(
            type="wechat",
            name="微信公众号",
            url="",
            source="wechat_mp",
            kind="article",
            weight=2.0,
            needs_wewe=True,
            wechat_article_url=article,
        )

    m_wewe = _WEWE_FEED.search(text)
    if m_wewe:
        mp_id = m_wewe.group(1)
        ext = (m_wewe.group(2) or "atom").lower()
        base = wewe_base.rstrip("/")
        return FeedDraft(
            type="wechat",
            name=mp_id,
            url=f"{base}/feeds/{mp_id}.{ext}",
            source="wechat_mp",
            kind="article",
            weight=2.0,
            mp_id=mp_id,
        )

    url = _normalize_http(text)
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        source, kind = _guess_source_kind(url)
        host = parsed.netloc.split(":")[0]
        name = host
        if source == "wechat_mp":
            raise DetectError("公众号请粘贴一篇文章链接，或 WeWe 的 /feeds/MP_WXS_….atom")
        return FeedDraft(
            type="rss",
            name=name,
            url=url,
            source=source,
            kind=kind,
            weight=2.0,
        )

    raise DetectError(
        "无法识别。试试：知乎主页 https://www.zhihu.com/people/账号、"
        "公众号文章 https://mp.weixin.qq.com/s/…、或任意 RSS URL"
    )
