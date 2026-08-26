"""Lab 5 · 正文抽取。

按站点走轻量适配器,通用兜底 trafilatura。
失败(太短 / 像导航)时 content 保持 None,只留标题 + summary。
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from lxml import html as lhtml

from core.schema import Item
from core.settings import Settings, load_settings
from core.store import Store
from core.text import html_to_text, normalize_paragraphs, strip_zhihu_footer

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "Fishnet/0.1 (+personal-research; not for redistribution)"
)

_NAV_RE = re.compile(
    r"登录|注册|首页|下载APP|相关推荐|热门评论|版权所有|ICP备|"
    r"隐私政策|关于我们|导航|分享到|点击查看|打开APP"
)
_WS_RE = re.compile(r"\s+")  # 仅标题/单行字段使用
_WSCN_ARTICLE_RE = re.compile(
    r"wallstreetcn\.com/(?:member/|premium/)?articles/(\d+)"
)
_WSCN_LIVE_RE = re.compile(r"wallstreetcn\.com/livenews/(\d+)")
_WSCN_CHART_RE = re.compile(r"wallstreetcn\.com/charts/\d+")
_ZHIHU_ANSWER_PATH_RE = re.compile(r"^/question/[^/]+/answer/")


@dataclass
class ExtractResult:
    url: str
    title: str | None = None
    text: str | None = None
    author: str | None = None
    score: float = 0.0
    tier: str = "none"  # full / partial / meta_only / blocked / error
    extractor: str = ""
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.tier in ("full", "partial")


def quality_score(text: str) -> float:
    """0~1。短、导航词密度高、碎行多 → 低分。

    用密度而不是绝对次数:正经文章里偶尔出现「首页」不该直接判死刑。
    """
    body = (text or "").strip()
    if not body:
        return 0.0
    n = len(body)
    if n < 80:
        return 0.05
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    short_ratio = sum(1 for ln in lines if len(ln) < 8) / max(len(lines), 1)
    nav_hits = len(_NAV_RE.findall(body))
    nav_density = nav_hits / max(n / 80.0, 1.0)
    length_s = min(n / 400.0, 1.0)
    return max(
        0.0,
        min(1.0, length_s - min(nav_density * 0.45, 0.55) - short_ratio * 0.25),
    )


def _clean(text: str) -> str:
    """单行字段(标题)才压空白。正文走 html_to_text / normalize_paragraphs。"""
    return _WS_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _xpath_plain(doc, xpath: str) -> str:
    nodes = doc.xpath(xpath)
    if not nodes:
        return ""
    parts = []
    for n in nodes:
        if hasattr(n, "itertext"):
            parts.append(" ".join(n.itertext()))
        else:
            parts.append(str(n))
    return _clean(" ".join(parts))


def _xpath_body(doc, xpath: str) -> str:
    nodes = doc.xpath(xpath)
    if not nodes:
        return ""
    chunks = []
    for n in nodes:
        if hasattr(n, "itertext"):
            try:
                html = lhtml.tostring(n, encoding="unicode", method="html")
            except Exception:
                html = " ".join(n.itertext())
            chunks.append(html_to_text(html))
        else:
            chunks.append(str(n))
    return normalize_paragraphs("\n\n".join(c for c in chunks if c.strip()))


def _xpath_text(doc, xpath: str) -> str:
    """兼容旧名:默认当标题/单行抽。"""
    return _xpath_plain(doc, xpath)


def _extract_weixin(page: str) -> tuple[str | None, str]:
    doc = lhtml.fromstring(page)
    title = _xpath_plain(doc, '//*[@id="activity-name"]') or _xpath_plain(doc, "//h1")
    body = _xpath_body(doc, '//*[@id="js_content"]')
    return (title or None, body)


def _extract_zhihu(page: str) -> tuple[str | None, str]:
    doc = lhtml.fromstring(page)
    title = (
        _xpath_plain(doc, '//*[contains(@class,"Post-Title")]')
        or _xpath_plain(doc, '//*[contains(@class,"QuestionHeader-title")]')
        or _xpath_plain(doc, "//h1")
    )
    body = _xpath_body(doc, '//*[contains(@class,"Post-RichText")]')
    if not body:
        body = _xpath_body(doc, '//*[contains(@class,"RichContent-inner")]')
    if not body:
        body = _xpath_body(doc, '//*[contains(@class,"QuestionAnswer-content")]')
    if body:
        body = strip_zhihu_footer(body)
    return (title or None, body)


def _extract_thepaper(page: str) -> tuple[str | None, str]:
    doc = lhtml.fromstring(page)
    title = _xpath_plain(doc, '//*[contains(@class,"index_title")]') or _xpath_plain(
        doc, "//h1"
    )
    body = _xpath_body(doc, '//*[contains(@class,"index_cententWrap")]')
    if not body:
        body = _xpath_body(doc, '//*[contains(@class,"news_txt")]')
    return (title or None, body)


_ADAPTERS: list[
    tuple[Callable[[str], bool], Callable[[str], tuple[str | None, str]], str]
] = [
    (lambda u: "mp.weixin.qq.com" in u, _extract_weixin, "weixin"),
    (
        lambda u: "zhihu.com" in u
        and ("/p/" in u or "zhuanlan" in u or "/answer/" in u),
        _extract_zhihu,
        "zhihu",
    ),
    (lambda u: "thepaper.cn" in u, _extract_thepaper, "thepaper"),
]


def _override_path_ok(host: str, path: str) -> bool:
    """个人订阅 override 只放行单篇,不放搜索/热榜问题页。"""
    if "weixin.qq.com" in host:
        return path.startswith("/s")
    if host == "zhuanlan.zhihu.com" or host.endswith(".zhuanlan.zhihu.com"):
        return path.startswith("/p/")
    if "zhihu.com" in host:
        return path.startswith("/p/") or bool(_ZHIHU_ANSWER_PATH_RE.match(path))
    return True


def wallstreetcn_api_url(url: str) -> str | None:
    """华尔街见闻前端是 JS 壳,正文走他们公开的 JSON API(llms.txt 允许引用)。"""
    m = _WSCN_ARTICLE_RE.search(url)
    if m:
        return f"https://api.wallstreetcn.com/apiv1/content/articles/{m.group(1)}?extract=1"
    m = _WSCN_LIVE_RE.search(url)
    if m:
        return f"https://api.wallstreetcn.com/apiv1/content/lives/{m.group(1)}"
    return None


def _strip_tags(html: str) -> str:
    return html_to_text(html or "")


def _parse_wallstreetcn_payload(data: dict) -> tuple[str | None, str]:
    if not isinstance(data, dict):
        return None, ""
    code = data.get("code")
    if code not in (20000, 200, None):
        return None, ""
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    title = payload.get("title")
    raw = payload.get("content") or payload.get("content_text") or ""
    if not isinstance(raw, str):
        raw = ""
    body = _strip_tags(raw) if "<" in raw else normalize_paragraphs(raw)
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None
    return title, body


def _trafilatura(page: str, url: str) -> tuple[str | None, str, str | None]:
    text = (
        trafilatura.extract(
            page,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=False,
        )
        or ""
    )
    meta = trafilatura.extract_metadata(page, default_url=url)
    title = getattr(meta, "title", None) if meta else None
    author = getattr(meta, "author", None) if meta else None
    return title, normalize_paragraphs(text), author


def _apply_quality(
    result: ExtractResult, *, min_chars: int, accept_short: bool = False
) -> ExtractResult:
    text = result.text or ""
    n = len(text)
    result.score = quality_score(text)
    if n >= min_chars and result.score >= 0.35:
        result.tier = "full"
        return result
    if n >= 150 and result.score >= 0.25:
        result.tier = "partial"
        return result
    # 华尔街见闻快讯全文经常只有一两百字,这就是完整条目,不是抽取失败。
    if accept_short and n >= 80 and result.score >= 0.20:
        result.tier = "partial"
        return result
    result.text = None
    result.tier = "meta_only"
    return result


class PoliteFetcher:
    """磁盘 HTML 缓存 + 同域间隔 + robots.txt。"""

    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl_seconds: float = 86400,
        delay_seconds: float = 1.5,
        timeout: float = 20.0,
        respect_robots: bool = True,
        robots_override_hosts: tuple[str, ...] = (),
        client: httpx.Client | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.delay_seconds = delay_seconds
        self.respect_robots = respect_robots
        self.robots_override_hosts = tuple(h.lower() for h in robots_override_hosts)
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def _write_cache(self, url: str, html: str) -> None:
        self._cache_path(url).write_text(html, encoding="utf-8")

    def _robots_ok(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        host = (parts.netloc or "").lower()
        for h in self.robots_override_hosts:
            if host == h or host.endswith("." + h):
                return _override_path_ok(host, parts.path)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = RobotFileParser()
            try:
                r = self._client.get(origin + "/robots.txt")
                if r.status_code >= 400:
                    rp.parse([])
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp.parse([])
            self._robots[origin] = rp
        return bool(rp.can_fetch(USER_AGENT, url))

    def _wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        now = time.monotonic()
        if last is not None:
            gap = self.delay_seconds - (now - last)
            if gap > 0:
                time.sleep(gap)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str) -> tuple[str, bool]:
        cached = self._read_cache(url)
        if cached is not None:
            return cached, True
        if not self._robots_ok(url):
            raise PermissionError(f"robots.txt 禁止抓取: {url}")
        self._wait(urlparse(url).netloc)
        r = self._client.get(url)
        r.raise_for_status()
        html = r.text
        self._write_cache(url, html)
        return html, False

    def get_json(self, url: str) -> dict:
        """同源 JSON(华尔街见闻 API)。走同样的间隔,不走 HTML 缓存。"""
        self._wait(urlparse(url).netloc)
        r = self._client.get(
            url,
            headers={
                "Accept": "application/json",
                "Referer": "https://wallstreetcn.com/",
            },
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError(f"JSON 根节点不是对象: {url}")
        return data


def _with_fallback(
    result: ExtractResult,
    fallback_text: str | None,
    min_n: int,
    *,
    accept_short: bool = False,
) -> ExtractResult:
    """HTML/API 失败时,用 RSS summary 顶上,避免报纸只剩标题。"""
    if result.ok or not fallback_text:
        return result
    fb = normalize_paragraphs(fallback_text)
    if len(fb) < 80:
        return result
    prev = result.tier
    result.text = fb
    result.extractor = (
        f"{result.extractor}+rss_fallback" if result.extractor else "rss_fallback"
    )
    if result.error:
        result.error = f"fallback after {prev}: {result.error}"
    return _apply_quality(result, min_chars=min_n, accept_short=accept_short)


def extract(
    url: str,
    html: str | None = None,
    *,
    fetcher: PoliteFetcher | None = None,
    min_chars: int | None = None,
    settings: Settings | None = None,
    fallback_text: str | None = None,
) -> ExtractResult:
    """按站点路由抽取。传入 html 时不发网络请求。"""
    cfg = settings or load_settings()
    min_n = int(min_chars if min_chars is not None else cfg.extract_min_chars)
    result = ExtractResult(url=url)
    own_fetcher = False
    page = html
    accept_short = bool(_WSCN_LIVE_RE.search(url))
    try:
        if page is None:
            if fetcher is None:
                fetcher = PoliteFetcher(
                    cfg.extract_cache_dir,
                    ttl_seconds=cfg.extract_cache_ttl_hours * 3600,
                    delay_seconds=cfg.extract_delay_seconds,
                    robots_override_hosts=cfg.extract_robots_override_hosts,
                )
                own_fetcher = True
            api = wallstreetcn_api_url(url)
            if api:
                try:
                    data = fetcher.get_json(api)
                    title, body = _parse_wallstreetcn_payload(data)
                    if body:
                        result.title = title
                        result.text = body
                        result.extractor = "wallstreetcn_api"
                        return _with_fallback(
                            _apply_quality(
                                result, min_chars=min_n, accept_short=accept_short
                            ),
                            fallback_text,
                            min_n,
                            accept_short=accept_short,
                        )
                except Exception as e:
                    result.error = f"wscn_api: {type(e).__name__}: {e}"
            if _WSCN_CHART_RE.search(url):
                result.extractor = "skip_chart"
                result.tier = "meta_only"
                result.error = "chart page, no article body"
                return _with_fallback(
                    result, fallback_text, min_n, accept_short=accept_short
                )
            page, from_cache = fetcher.get(url)
            result.from_cache = from_cache

        title: str | None = None
        body = ""
        author: str | None = None
        extractor = ""
        for match, fn, name in _ADAPTERS:
            if match(url):
                try:
                    title, body = fn(page)
                    extractor = name
                except Exception:
                    title, body = None, ""
                break

        if len(body or "") < min_n:
            t2, b2, a2 = _trafilatura(page, url)
            if len(b2) > len(body or ""):
                body = b2
                title = title or t2
                author = a2
                extractor = f"{extractor}+trafilatura" if extractor else "trafilatura"
            elif not extractor:
                title, body, author = t2, b2, a2
                extractor = "trafilatura"

        result.title = title
        result.text = body or None
        if "zhihu.com" in url and result.text:
            result.text = strip_zhihu_footer(result.text)
        result.author = author
        result.extractor = extractor or "none"
        return _with_fallback(
            _apply_quality(result, min_chars=min_n, accept_short=accept_short),
            fallback_text,
            min_n,
            accept_short=accept_short,
        )
    except PermissionError as e:
        result.tier = "blocked"
        result.error = str(e)
        return _with_fallback(
            result, fallback_text, min_n, accept_short=accept_short
        )
    except Exception as e:
        result.tier = "error"
        result.error = f"{type(e).__name__}: {e}"
        return _with_fallback(
            result, fallback_text, min_n, accept_short=accept_short
        )
    finally:
        if own_fetcher and fetcher is not None:
            fetcher.close()


def fill_item_content(
    item: Item,
    *,
    fetcher: PoliteFetcher | None = None,
    min_chars: int | None = None,
    settings: Settings | None = None,
) -> ExtractResult:
    """抽取并回填 Item.content。质量不够则保持 None(降级为标题+summary)。"""
    result = extract(
        item.url,
        fetcher=fetcher,
        min_chars=min_chars,
        settings=settings,
        fallback_text=item.summary,
    )
    if result.ok and result.text:
        item.content = result.text
        if result.author and not item.author:
            item.author = result.author
    return result


def enrich_store(
    store: Store,
    *,
    limit: int = 20,
    fetcher: PoliteFetcher | None = None,
    settings: Settings | None = None,
) -> dict:
    """把库里缺正文的条目抽一遍,回填 content。"""
    cfg = settings or load_settings()
    own = fetcher is None
    fetcher = fetcher or PoliteFetcher(
        cfg.extract_cache_dir,
        ttl_seconds=cfg.extract_cache_ttl_hours * 3600,
        delay_seconds=cfg.extract_delay_seconds,
        robots_override_hosts=cfg.extract_robots_override_hosts,
    )
    stats = {"ok": 0, "degraded": 0, "blocked": 0, "error": 0, "cached": 0}
    try:
        for it in store.items_missing_content(limit=limit):
            r = fill_item_content(it, fetcher=fetcher, settings=cfg)
            host = urlparse(it.url).netloc
            title = (it.title or "")[:32]
            print(
                f"  [{r.tier:9s}] {host:24s} {title!r} "
                f"{r.extractor} n={len(r.text or '')} {(r.error or '')[:80]}"
            )
            if r.from_cache:
                stats["cached"] += 1
            if r.ok and r.text:
                store.update_content(it.content_hash, r.text)
                stats["ok"] += 1
            elif r.tier == "blocked":
                stats["blocked"] += 1
            elif r.tier == "error":
                stats["error"] += 1
            else:
                stats["degraded"] += 1
    finally:
        if own:
            fetcher.close()
    return stats
