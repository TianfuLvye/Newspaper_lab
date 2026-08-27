"""配图管道:从微信 / 华尔街见闻 / 知乎收候选,启发式去噪,出报时 LLM 挑 1–3 张并下载。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from lxml import html as lhtml

from core.schema import Item
from core.text import readable_body

PHOTO_HOST_MARKERS = (
    "mp.weixin.qq.com",
    "wallstreetcn.com",
    "zhihu.com",
)
MAX_CANDIDATES = 8
MAX_KEEP = 3
MAX_IMAGE_BYTES = 8 * 1024 * 1024
COVER_ROLES = {"cover", "og"}

_SKIP_RE = re.compile(
    r"avatar|emoji|qrcode|qr[_-]?code|icon|logo|badge|spacer|pixel|1x1|"
    r"placeholder|tracking|mmhead|user[-_]head|profile_avatar|"
    r"wx_fmt=gif|formula|equation|latex|zhimg\.com/equation|"
    r"/s50|/s64|_s\.jpg|_is\.jpg|share_card|js_next_card",
    re.I,
)
_SKIP_CLASS_RE = re.compile(
    r"avatar|emoji|qrcode|icon|logo|badge|equation|formula|ProfileHeader|"
    r"AuthorInfo|UserLink|share",
    re.I,
)
_SIZE_QS = {"wx_fmt", "tp", "from", "w", "h", "width", "height", "size"}

PICK_PROMPT = """你是一份个人报纸的图片编辑。从候选图里挑最多 3 张适合印在早报/晚报上的配图。
丢掉头像、图标、二维码、表情、广告、无关装饰。优先:封面、信息图、正文关键场景。
只输出 JSON: {"keep": [<候选下标>], "captions": ["..."]}
captions 与 keep 等长,每条不超过 12 字,没有合适说明就空字符串。
"""


def is_photo_host(url: str) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return any(m in host or m in (url or "") for m in PHOTO_HOST_MARKERS)


def harvest_page_images(url: str, html: str | None) -> list[dict]:
    if not html or not is_photo_host(url):
        return []
    if "mp.weixin.qq.com" in url:
        return prune_candidates(harvest_weixin(html, page_url=url))
    if "zhihu.com" in url:
        return prune_candidates(harvest_zhihu(html, page_url=url))
    if "wallstreetcn.com" in url:
        raw = harvest_og_image(html, page_url=url)
        raw.extend(harvest_html_images(html, page_url=url, role="body"))
        return prune_candidates(raw)
    return []


def harvest_wscn_payload(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    cover = _first_url(
        payload.get("image"),
        payload.get("image_uri"),
        payload.get("uri"),
        payload.get("cover"),
    )
    if cover:
        out.append({"url": cover, "alt": str(payload.get("title") or ""), "role": "cover"})
    extra = payload.get("images")
    if isinstance(extra, list):
        for item in extra:
            u = _first_url(item)
            if u:
                out.append({"url": u, "alt": "", "role": "body"})
    raw = payload.get("content") or payload.get("content_text") or ""
    if isinstance(raw, str) and "<" in raw:
        out.extend(harvest_html_images(raw, page_url="https://wallstreetcn.com/", role="body"))
    return prune_candidates(out)


def harvest_rss_html(html_blobs: list[str], *, page_url: str) -> list[dict]:
    if not is_photo_host(page_url):
        return []
    out: list[dict] = []
    for blob in html_blobs:
        if not blob or "<" not in blob:
            continue
        if "mp.weixin.qq.com" in page_url:
            out.extend(harvest_weixin(blob, page_url=page_url))
        elif "zhihu.com" in page_url:
            out.extend(harvest_zhihu(blob, page_url=page_url))
        else:
            out.extend(harvest_og_image(blob, page_url=page_url))
            out.extend(harvest_html_images(blob, page_url=page_url, role="body"))
    return prune_candidates(out)


def harvest_weixin(html: str, *, page_url: str = "") -> list[dict]:
    doc = _parse_html(html)
    if doc is None:
        return []
    out = harvest_og_image(html, page_url=page_url)
    nodes = doc.xpath('//*[@id="js_content"]//img') or doc.xpath("//img")
    for img in nodes:
        rec = _img_record(img, page_url=page_url, role="body")
        if rec:
            out.append(rec)
    return out


def harvest_zhihu(html: str, *, page_url: str = "") -> list[dict]:
    doc = _parse_html(html)
    if doc is None:
        return []
    out = harvest_og_image(html, page_url=page_url)
    nodes = doc.xpath(
        '//*[contains(@class,"Post-RichText")]//img'
        ' | //*[contains(@class,"RichContent-inner")]//img'
        ' | //*[contains(@class,"QuestionAnswer-content")]//img'
        ' | //*[contains(@class,"RichText")]//img'
    )
    if not nodes:
        nodes = doc.xpath("//img")
    for img in nodes:
        rec = _img_record(img, page_url=page_url, role="body")
        if rec:
            out.append(rec)
    return out


def harvest_html_images(html: str, *, page_url: str = "", role: str = "body") -> list[dict]:
    doc = _parse_html(html)
    if doc is None:
        return []
    out: list[dict] = []
    for img in doc.xpath("//img"):
        rec = _img_record(img, page_url=page_url, role=role)
        if rec:
            out.append(rec)
    return out


def harvest_og_image(html: str, *, page_url: str = "") -> list[dict]:
    doc = _parse_html(html)
    if doc is None:
        return []
    vals = doc.xpath('//meta[@property="og:image"]/@content | //meta[@name="og:image"]/@content')
    out: list[dict] = []
    for v in vals:
        url = _abs_url(str(v).strip(), page_url)
        if url.startswith("http"):
            out.append({"url": url, "alt": "", "role": "cover"})
    return out


def prune_candidates(cands: list[dict], *, limit: int = MAX_CANDIDATES) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for raw in cands or []:
        if not isinstance(raw, dict):
            continue
        url = _abs_url(str(raw.get("url") or "").strip(), "")
        if not url.startswith("http"):
            continue
        blob = f"{url} {raw.get('alt') or ''} {raw.get('class') or ''}"
        if _SKIP_RE.search(blob):
            continue
        key = _canon_image_url(url)
        if key in seen:
            continue
        seen.add(key)
        rec = {
            "url": url,
            "alt": str(raw.get("alt") or "")[:80],
            "role": str(raw.get("role") or "unknown"),
        }
        if raw.get("width"):
            rec["width"] = int(raw["width"])
        if raw.get("height"):
            rec["height"] = int(raw["height"])
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def pick_images(
    title: str,
    body: str,
    candidates: list[dict],
    *,
    max_keep: int = MAX_KEEP,
    llm_fn=None,
) -> list[dict]:
    """LLM 挑 keep 下标;失败或没 Key 走启发式。"""
    pruned = prune_candidates(candidates)
    if not pruned:
        return []
    if llm_fn is not None:
        try:
            chosen = llm_fn(title, body, pruned)
            return _apply_keep(pruned, chosen, max_keep=max_keep)
        except Exception:
            pass
    elif os.environ.get("FISHNET_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        try:
            chosen = _llm_pick(title, body, pruned)
            return _apply_keep(pruned, chosen, max_keep=max_keep)
        except Exception:
            pass
    return heuristic_pick(pruned, max_keep=max_keep)


def heuristic_pick(candidates: list[dict], *, max_keep: int = MAX_KEEP) -> list[dict]:
    if not candidates:
        return []
    covers = [c for c in candidates if c.get("role") in COVER_ROLES]
    rest = [c for c in candidates if c.get("role") not in COVER_ROLES]
    ordered = covers + rest
    n = min(len(ordered), max_keep)
    if not covers:
        n = min(n, 2)
    return ordered[:n]


def image_markdown(refs: list[dict]) -> str:
    if not refs:
        return ""
    lines = []
    for r in refs:
        cap = str(r.get("caption") or r.get("alt") or "").replace("]", " ")
        src = r.get("url") or ""
        if src:
            lines.append(f"![{cap}]({src})")
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def save_image_bytes(data: bytes, dest_dir: Path) -> dict | None:
    """把字节写成 JPEG/PNG,返回相对路径与宽高。webp 转 JPEG。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image as PILImage
    except ImportError:
        return None
    try:
        im = PILImage.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    w, h = im.size
    if w < 40 or h < 40:
        return None
    fmt = (im.format or "").upper()
    digest = hashlib.sha256(data).hexdigest()[:16]
    if fmt == "PNG" and (im.mode in ("RGBA", "LA") or "transparency" in im.info):
        ext = "png"
        out = dest_dir / f"{digest}.png"
        if not out.exists():
            im.save(out, format="PNG")
    else:
        ext = "jpg"
        out = dest_dir / f"{digest}.jpg"
        if not out.exists():
            rgb = im.convert("RGB") if im.mode != "RGB" else im
            rgb.save(out, format="JPEG", quality=88)
    return {
        "url": f"images/{out.name}",
        "width": int(w),
        "height": int(h),
        "path": str(out),
        "ext": ext,
    }


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:
        return 1200, 800


@dataclass
class ImageMaterializer:
    """出报时把候选下载到期次 images/,并生成 Markdown。"""

    dest: Path
    fetcher: object | None = None
    llm_fn: object | None = None
    _cache: dict[str, list[dict]] = field(default_factory=dict)
    _owns_fetcher: bool = False

    def __post_init__(self) -> None:
        self.dest = Path(self.dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        if self.fetcher is None:
            from core.settings import load_settings
            from enrich.extract import PoliteFetcher

            cfg = load_settings()
            self.fetcher = PoliteFetcher(
                cfg.extract_cache_dir,
                ttl_seconds=cfg.extract_cache_ttl_hours * 3600,
                delay_seconds=cfg.extract_delay_seconds,
                robots_override_hosts=cfg.extract_robots_override_hosts,
            )
            self._owns_fetcher = True

    def close(self) -> None:
        if self._owns_fetcher and self.fetcher is not None:
            close = getattr(self.fetcher, "close", None)
            if close:
                close()

    def markdown_for(self, item: Item) -> str:
        return image_markdown(self.ensure(item))

    def ensure(self, item: Item) -> list[dict]:
        key = item.content_hash or item.url
        if key in self._cache:
            return self._cache[key]
        cands = list(item.images or [])
        if not cands:
            self._cache[key] = []
            return []
        body = readable_body(item) or item.summary or ""
        picked = pick_images(
            item.title or "",
            body,
            cands,
            llm_fn=self.llm_fn,
        )
        local: list[dict] = []
        for cand in picked:
            saved = self._download(cand, referer=item.url)
            if saved:
                local.append(saved)
        self._cache[key] = local
        return local

    def _download(self, cand: dict, *, referer: str) -> dict | None:
        url = cand.get("url") or ""
        get_bytes = getattr(self.fetcher, "get_bytes", None)
        if not url or get_bytes is None:
            return None
        try:
            data = get_bytes(url, referer=referer)
        except Exception:
            return None
        saved = save_image_bytes(data, self.dest / "images")
        if not saved:
            return None
        saved["alt"] = cand.get("alt") or ""
        saved["caption"] = cand.get("caption") or cand.get("alt") or ""
        saved["role"] = cand.get("role") or "body"
        return saved


def _apply_keep(pruned: list[dict], chosen: dict, *, max_keep: int) -> list[dict]:
    keep = chosen.get("keep") if isinstance(chosen, dict) else None
    captions = chosen.get("captions") if isinstance(chosen, dict) else None
    if not isinstance(keep, list):
        return heuristic_pick(pruned, max_keep=max_keep)
    out: list[dict] = []
    caps = captions if isinstance(captions, list) else []
    for j, idx in enumerate(keep):
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if i < 0 or i >= len(pruned):
            continue
        rec = dict(pruned[i])
        if j < len(caps) and caps[j]:
            rec["caption"] = str(caps[j])[:24]
        out.append(rec)
        if len(out) >= max_keep:
            break
    return out or heuristic_pick(pruned, max_keep=max_keep)


def _llm_pick(title: str, body: str, pruned: list[dict]) -> dict:
    import httpx

    key = os.environ.get("FISHNET_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = (
        os.environ.get("FISHNET_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = os.environ.get("FISHNET_LLM_MODEL") or "gpt-4o-mini"
    lines = []
    for i, c in enumerate(pruned):
        lines.append(f"{i}. role={c.get('role')} alt={c.get('alt') or '-'} url={c['url'][:180]}")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": PICK_PROMPT.strip()},
            {
                "role": "user",
                "content": (
                    f"标题: {title}\n正文开头: {(body or '')[:400]}\n候选:\n"
                    + "\n".join(lines)
                ),
            },
        ],
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("llm pick not an object")
    return data


def _parse_html(html: str):
    if not (html or "").strip():
        return None
    try:
        return lhtml.fromstring(html)
    except Exception:
        return None


def _img_record(img, *, page_url: str, role: str) -> dict | None:
    cls = " ".join(img.get("class") or []) if isinstance(img.get("class"), list) else (img.get("class") or "")
    if _SKIP_CLASS_RE.search(cls):
        return None
    src = (
        img.get("data-src")
        or img.get("data-original")
        or img.get("data-actualsrc")
        or img.get("data-lazy-src")
        or img.get("src")
        or ""
    ).strip()
    if not src or src.startswith("data:"):
        return None
    url = _abs_url(src, page_url)
    alt = (img.get("alt") or img.get("data-caption") or "").strip()
    rec: dict = {"url": url, "alt": alt, "role": role, "class": cls}
    w = img.get("data-w") or img.get("width")
    h = img.get("data-h") or img.get("height")
    try:
        if w:
            rec["width"] = int(str(w).replace("px", ""))
        if h:
            rec["height"] = int(str(h).replace("px", ""))
    except ValueError:
        pass
    return rec


def _abs_url(src: str, page_url: str) -> str:
    src = (src or "").strip()
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if page_url:
        return urljoin(page_url, src)
    return src


def _canon_image_url(url: str) -> str:
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in _SIZE_QS]
    kept.sort()
    path = re.sub(r"/0$", "/640", parts.path)
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(kept), ""))


def _first_url(*vals) -> str:
    for v in vals:
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, dict):
            for k in ("uri", "url", "src", "image"):
                u = v.get(k)
                if isinstance(u, str) and u.startswith("http"):
                    return u
    return ""
