"""Fishnet 数据契约 —— Lab 0 标准答案。

全系统唯一的数据结构。任何 collector 的输出、任何 pipeline 的输入,
都必须是 Item。改这个文件等于改全系统的 ABI,请慎重。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class Source(str, Enum):
    WEIBO = "weibo"
    ZHIHU = "zhihu"
    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    XHS = "xiaohongshu"
    WECHAT_MP = "wechat_mp"
    TOUTIAO = "toutiao"      # 今日头条热榜(细来源;勿再糊进 news)
    THEPAPER = "thepaper"     # 澎湃新闻热榜
    NEWS = "news"            # 其它泛新闻源的粗分类
    FINANCE = "finance"
    RSS = "rss"
    OTHER = "other"


class Kind(str, Enum):
    HOTLIST = "hotlist"
    ARTICLE = "article"
    POST = "post"
    VIDEO = "video"
    QUOTE = "quote"


# ---------------------------------------------------------------------------
# URL 归一化 —— Lab 0 的核心练习
# ---------------------------------------------------------------------------

# 追踪参数黑名单。前缀匹配 + 精确匹配两套。
_TRACKING_PREFIXES = ("utm_", "spm_", "_hs", "mc_", "pk_", "at_", "ga_")
_TRACKING_EXACT = {
    "spm", "from", "from_source", "from_spmid", "share_token", "share_source",
    "share_medium", "share_plat", "share_tag", "share_session_id", "shareurl",
    "timestamp", "unique_k", "vd_source", "buvid", "up_id", "seid",
    "fbclid", "gclid", "yclid", "msclkid", "igshid", "ref", "ref_src",
    "referer", "referrer", "s", "src", "source", "scene", "sessionid",
    "chksm", "srcid", "sharer_sharetime", "sharer_shareid", "exportkey",
    "device_id", "traceid", "trace_id", "logid", "log_id", "click_id",
    "app_platform", "app_version", "channel", "tt_from", "u_code",
}

# 某些站点的参数是内容标识,绝不能剥。按 host 白名单保护。
_ESSENTIAL_PARAMS = {
    "mp.weixin.qq.com": {"__biz", "mid", "idx", "sn"},
    "www.bilibili.com": {"p", "t"},
    "www.youtube.com": {"v", "list"},
    "www.zhihu.com": set(),
}

# 移动端 / 镜像域名 → 规范域名
_HOST_CANONICAL = {
    "m.weibo.cn": "weibo.com",
    "www.weibo.com": "weibo.com",
    "m.zhihu.com": "www.zhihu.com",
    "zhuanlan.zhihu.com": "zhuanlan.zhihu.com",  # 专栏是独立命名空间, 保留
    "zhihu.com": "www.zhihu.com",
    "m.bilibili.com": "www.bilibili.com",
    "b23.tv": "b23.tv",  # 短链无法本地还原, 交给 collector 展开
    "bilibili.com": "www.bilibili.com",
}


def normalize_url(url: str) -> str:
    """把同一内容的不同 URL 变体归一成同一个字符串。

    处理:scheme 统一 https、host 小写去 www 变体、剥追踪参数、
    参数排序、去空 query、去末尾斜杠、丢弃 fragment(除非是 SPA 锚点)。
    """
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    parts = urlsplit(url)
    host = parts.hostname or ""
    host = host.lower()
    host = _HOST_CANONICAL.get(host, host)

    essential = _ESSENTIAL_PARAMS.get(host)
    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        lk = k.lower()
        if essential is not None:
            # 白名单模式:该站点只保留确定有意义的参数
            if lk in essential:
                kept.append((k, v))
            continue
        if lk in _TRACKING_EXACT or lk.startswith(_TRACKING_PREFIXES):
            continue
        kept.append((k, v))
    kept.sort()

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    return urlunsplit(("https", host, path, urlencode(kept), ""))


# ---------------------------------------------------------------------------
# 标题归一化 —— 用于 hash,不改变展示用的原标题
# ---------------------------------------------------------------------------

_TITLE_NOISE = re.compile(
    # 单个 ~ 即可;写成 ~~ 会触发 Python 3.13+ 的集合运算 FutureWarning
    r"[\s\u3000]+|[「」『』【】\[\]()《》<>“”\"'’‘·・,。.!?\:;、~\-—_|｜#]+"
)


def normalize_title(title: str) -> str:
    """去掉标点、空白、装饰符号,只留内容字符。

    这样「宇树科技发布新机器人!」和「宇树科技发布新机器人」是同一条。
    """
    t = (title or "").strip().lower()
    t = re.sub(r"^\d+[.、]\s*", "", t)        # 去掉热榜自带的序号前缀
    t = re.sub(r"[\u200b-\u200f\ufeff]", "", t)  # 零宽字符
    return _TITLE_NOISE.sub("", t)


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------

@dataclass
class Item:
    source: Source
    kind: Kind
    title: str
    url: str

    summary: str | None = None
    content: str | None = None
    author: str | None = None
    author_id: str | None = None

    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    rank: int | None = None
    heat: float | None = None
    tags: list[str] = field(default_factory=list)

    collector: str = ""
    raw: dict = field(default_factory=dict)

    # 加工层回填
    score: float | None = None
    cluster_id: str | None = None
    llm_summary: str | None = None
    used_in: str | None = None

    content_hash: str = ""

    # 配图候选(url / alt / width / height / role)。出报时再筛 1–3 张下载。
    images: list[dict] = field(default_factory=list)

    # --- 派生 ---
    def normalized_url(self) -> str:
        return normalize_url(self.url)

    def compute_hash(self) -> str:
        """去重主键。

        设计要点:
        1. 只用「随时间不变」的字段。rank / heat / fetched_at 一律排除,
           否则同一条热搜每次采集都会变成新记录。
        2. URL 与标题双保险:URL 归一化后为空(有些热榜不给链接)时,
           退化为 source + 标题;两者都有时用 URL 为主、标题为辅。
        3. 用 sha256 截断 32 位十六进制 = 128 bit,碰撞概率可忽略。
        """
        nurl = self.normalized_url()
        ntitle = normalize_title(self.title)
        if nurl:
            basis = f"{self.source.value}|url|{nurl}"
        else:
            basis = f"{self.source.value}|title|{ntitle}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    def __post_init__(self):
        if isinstance(self.source, str):
            self.source = Source(self.source)
        if isinstance(self.kind, str):
            self.kind = Kind(self.kind)
        if self.published_at and self.published_at.tzinfo is None:
            # naive datetime 一律当作北京时间处理后转 UTC。
            # 绝不允许 naive datetime 进入存储层。
            from datetime import timedelta
            self.published_at = self.published_at.replace(
                tzinfo=timezone(timedelta(hours=8))
            ).astimezone(timezone.utc)
        if self.fetched_at.tzinfo is None:
            self.fetched_at = self.fetched_at.replace(tzinfo=timezone.utc)
        if not self.content_hash:
            self.content_hash = self.compute_hash()

    # --- 序列化 ---
    def to_row(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["kind"] = self.kind.value
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        d["fetched_at"] = self.fetched_at.isoformat()
        d["tags"] = json.dumps(self.tags, ensure_ascii=False)
        d["images"] = json.dumps(self.images, ensure_ascii=False) if self.images else None
        d.pop("raw", None)
        return d

    @property
    def text_for_embedding(self) -> str:
        body = self.content or self.summary or ""
        return f"{self.title}\n{body[:2000]}"

    @property
    def length(self) -> int:
        return len(self.content or self.summary or self.title)
