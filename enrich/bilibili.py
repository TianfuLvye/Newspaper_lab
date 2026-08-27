"""B 站白名单：列出 UP 最近投稿和合集全量 BV。不下载、不转写。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx

from core.schema import Item, Kind, Source
from core.settings import Settings, load_bilibili_whitelist, load_settings
from core.store import Store

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SEASON_API = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
PAGE_SIZE = 50
UP_RSS_LIMIT = 40

_MID_RE = re.compile(r"space\.bilibili\.com/(\d+)", re.I)
_LISTS_RE = re.compile(r"space\.bilibili\.com/(\d+)/lists/(\d+)", re.I)
_OLD_SID_RE = re.compile(r"collectiondetail\?[^#]*sid=(\d+)", re.I)
_BV_RE = re.compile(r"(BV[0-9A-Za-z]+)")


@dataclass(frozen=True)
class BiliTarget:
    kind: str  # up | season
    mid: str
    name: str
    url: str
    season_id: str | None = None
    drip: bool = False


@dataclass
class BiliVideo:
    bvid: str
    title: str
    duration: int | None = None
    pubdate: int | None = None
    pic: str | None = None
    author: str | None = None


def parse_bili_url(url: str, *, name: str = "", drip: bool = False) -> BiliTarget:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty bilibili url")
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    lists = _LISTS_RE.search(raw)
    old = _OLD_SID_RE.search(raw)
    if lists:
        kind = (qs.get("type") or ["season"])[0].lower()
        if kind == "series":
            raise ValueError(f"type=series is not a season collection: {raw}")
        if kind not in ("season",):
            raise ValueError(f"unsupported list type {kind!r}: {raw}")
        return BiliTarget(
            kind="season",
            mid=lists.group(1),
            season_id=lists.group(2),
            name=name,
            url=raw,
            drip=drip,
        )
    if old:
        mid_m = _MID_RE.search(raw)
        if not mid_m:
            raise ValueError(f"collection url missing mid: {raw}")
        return BiliTarget(
            kind="season",
            mid=mid_m.group(1),
            season_id=old.group(1),
            name=name,
            url=raw,
            drip=drip,
        )
    mid_m = _MID_RE.search(raw)
    if mid_m:
        return BiliTarget(kind="up", mid=mid_m.group(1), name=name, url=raw, drip=drip)
    raise ValueError(f"not a space or season url: {raw}")


def load_targets(rows: dict | None = None) -> list[BiliTarget]:
    cfg = rows if rows is not None else load_bilibili_whitelist()
    out: list[BiliTarget] = []
    for row in cfg.get("ups") or []:
        out.append(parse_bili_url(str(row.get("url") or ""), name=str(row.get("name") or "")))
    for row in cfg.get("collections") or []:
        out.append(
            parse_bili_url(
                str(row.get("url") or ""),
                name=str(row.get("name") or ""),
                drip=bool(row.get("drip")),
            )
        )
    return out


def _client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        },
        follow_redirects=True,
    )


def fetch_season_videos(
    target: BiliTarget,
    *,
    client: httpx.Client | None = None,
) -> tuple[dict, list[BiliVideo]]:
    if target.kind != "season" or not target.season_id:
        raise ValueError("not a season target")
    own = client is None
    client = client or _client()
    videos: list[BiliVideo] = []
    meta: dict = {}
    try:
        page = 1
        total = None
        while True:
            r = client.get(
                SEASON_API,
                params={
                    "mid": target.mid,
                    "season_id": target.season_id,
                    "page_num": page,
                    "page_size": PAGE_SIZE,
                    "sort_reverse": "false",
                },
            )
            r.raise_for_status()
            body = r.json()
            if body.get("code") != 0:
                raise RuntimeError(f"season api {body.get('code')}: {body.get('message')}")
            data = body.get("data") or {}
            meta = data.get("meta") or meta
            page_info = data.get("page") or {}
            if total is None:
                total = int(meta.get("total") or page_info.get("total") or 0)
            batch = []
            for row in data.get("archives") or []:
                bvid = str(row.get("bvid") or "")
                if not bvid:
                    continue
                batch.append(
                    BiliVideo(
                        bvid=bvid,
                        title=str(row.get("title") or bvid),
                        duration=row.get("duration"),
                        pubdate=row.get("pubdate") or row.get("ctime"),
                        pic=row.get("pic"),
                        author=str(meta.get("name") or target.name or ""),
                    )
                )
            videos.extend(batch)
            if not batch:
                break
            if total and len(videos) >= total:
                break
            if len(batch) < PAGE_SIZE:
                break
            page += 1
            if page > 40:
                break
    finally:
        if own:
            client.close()
    seen: set[str] = set()
    uniq: list[BiliVideo] = []
    for v in videos:
        if v.bvid in seen:
            continue
        seen.add(v.bvid)
        uniq.append(v)
    return meta, uniq


def fetch_up_videos(
    target: BiliTarget,
    *,
    rsshub_url: str,
    client: httpx.Client | None = None,
    limit: int = UP_RSS_LIMIT,
) -> list[BiliVideo]:
    if target.kind != "up":
        raise ValueError("not an up target")
    feed_url = f"{rsshub_url.rstrip('/')}/bilibili/user/video/{target.mid}?limit={limit}"
    own = client is None
    client = client or _client()
    try:
        r = client.get(feed_url)
        r.raise_for_status()
        text = r.text
    finally:
        if own:
            client.close()
    if "<item>" not in text and "<entry>" not in text:
        raise RuntimeError(f"rsshub up feed is not rss: {text[:180]}")
    parsed = feedparser.parse(text)
    videos: list[BiliVideo] = []
    for entry in parsed.entries or []:
        link = str(entry.get("link") or "")
        m = _BV_RE.search(link) or _BV_RE.search(str(entry.get("title") or ""))
        if not m:
            continue
        pub = None
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        if st:
            try:
                pub = int(datetime(*st[:6], tzinfo=timezone.utc).timestamp())
            except (OverflowError, TypeError, ValueError):
                pub = None
        videos.append(
            BiliVideo(
                bvid=m.group(1),
                title=str(entry.get("title") or m.group(1)),
                pubdate=pub,
                author=target.name or None,
            )
        )
    return videos


def catalog_path(season_id: str, *, root: Path | None = None) -> Path:
    base = root or (load_settings().db_path.parent / "bilibili_seasons")
    return Path(base) / f"{season_id}.json"


def write_season_catalog(
    target: BiliTarget,
    meta: dict,
    videos: list[BiliVideo],
    *,
    path: Path | None = None,
) -> Path:
    dest = path or catalog_path(target.season_id or "")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if dest.exists():
        try:
            prev = json.loads(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    drip = dict(prev.get("drip") or {})
    drip.setdefault("enabled", target.drip)
    drip.setdefault("index", 0)
    drip.setdefault("order", "collection")
    payload = {
        "mid": target.mid,
        "season_id": target.season_id,
        "name": meta.get("name") or target.name,
        "url": target.url,
        "total": int(meta.get("total") or len(videos)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "drip": drip,
        "videos": [
            {
                "bvid": v.bvid,
                "title": v.title,
                "duration": v.duration,
                "pubdate": v.pubdate,
                "pic": v.pic,
            }
            for v in videos
        ],
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def videos_to_items(
    videos: list[BiliVideo],
    *,
    collector: str,
    author: str | None = None,
    tags: list[str] | None = None,
) -> list[Item]:
    items: list[Item] = []
    for v in videos:
        published = None
        if v.pubdate:
            published = datetime.fromtimestamp(int(v.pubdate), tz=timezone.utc)
        images = []
        if v.pic:
            images.append({"url": v.pic, "alt": v.title, "role": "cover"})
        items.append(
            Item(
                source=Source.BILIBILI,
                kind=Kind.VIDEO,
                title=v.title,
                url=f"https://www.bilibili.com/video/{v.bvid}",
                author=v.author or author,
                author_id=None,
                published_at=published,
                collector=collector,
                tags=list(tags or []),
                images=images,
                raw={"bvid": v.bvid, "duration": v.duration},
            )
        )
    return items


def enrich_bilibili(
    store: Store,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    catalog_dir: Path | None = None,
    targets: list[BiliTarget] | None = None,
) -> dict:
    """列出白名单视频并入库。失败隔离到单个 target。"""
    cfg = settings or load_settings()
    stats = {
        "ups": 0,
        "collections": 0,
        "videos_new": 0,
        "videos_dup": 0,
        "catalog": 0,
        "error": 0,
    }
    wanted = targets if targets is not None else load_targets()
    own = client is None
    client = client or _client()
    try:
        for target in wanted:
            try:
                if target.kind == "season":
                    meta, videos = fetch_season_videos(target, client=client)
                    write_season_catalog(
                        target,
                        meta,
                        videos,
                        path=(catalog_dir / f"{target.season_id}.json") if catalog_dir else None,
                    )
                    stats["catalog"] = max(stats["catalog"], len(videos))
                    stats["collections"] += 1
                    items = videos_to_items(
                        videos,
                        collector=f"bili_season_{target.season_id}",
                        author=target.name or str(meta.get("name") or ""),
                        tags=["bili-whitelist", f"season:{target.season_id}"],
                    )
                    for it in items:
                        it.author_id = target.mid
                else:
                    videos = fetch_up_videos(target, rsshub_url=cfg.rsshub_url, client=client)
                    stats["ups"] += 1
                    items = videos_to_items(
                        videos,
                        collector=f"bili_up_{target.mid}",
                        author=target.name,
                        tags=["bili-whitelist", f"up:{target.mid}"],
                    )
                    for it in items:
                        it.author_id = target.mid
                new, dup = store.upsert_items(items)
                stats["videos_new"] += new
                stats["videos_dup"] += dup
            except Exception as exc:
                stats["error"] += 1
                print(f"  [bili error] {target.kind} {target.url} {exc}")
    finally:
        if own:
            client.close()
    return stats
