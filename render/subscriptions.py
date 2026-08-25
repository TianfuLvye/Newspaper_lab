"""Lab 3 / Lab 6:订阅更新。出报用可读正文,不再用「标题+打开链接」表。"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.schema import Item, Kind
from core.settings import load_feeds, load_settings
from core.store import Store

CST = timezone(timedelta(hours=8))
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff\-]+", re.UNICODE)


def _rss_collector_names(feeds: list[dict] | None = None) -> set[str]:
    from collectors.rss_generic import slugify

    rows = feeds if feeds is not None else load_feeds()
    return {f"rss_{slugify(r['name'])}" for r in rows}


def _feed_weights(feeds: list[dict] | None = None) -> dict[str, float]:
    from collectors.rss_generic import slugify

    rows = feeds if feeds is not None else load_feeds()
    return {f"rss_{slugify(r['name'])}": float(r.get("weight") or 1.0) for r in rows}


def item_body(it: Item) -> str:
    """报纸上能印出来的文字:正文优先,否则摘要。"""
    return ((it.content or it.summary or "")).strip()


def item_slug(it: Item, *, n: int | None = None) -> str:
    raw = (it.title or it.content_hash)[:40]
    slug = _SLUG_RE.sub("", raw.replace(" ", "-")) or it.content_hash[:8]
    prefix = f"{n:02d}-" if n is not None else ""
    return f"{prefix}{slug}"


def _round_robin(
    items: list[Item],
    *,
    limit: int,
    max_per_collector: int,
    weights: dict[str, float] | None = None,
) -> list[Item]:
    """每个订阅源轮流取,避免一个榜单占满整版。同网内优先有正文的。"""
    buckets: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        buckets[it.collector].append(it)

    def recency(it: Item) -> float:
        t = it.published_at or it.fetched_at
        return t.timestamp() if t else 0.0

    queues: dict[str, deque[Item]] = {}
    for name, group in buckets.items():
        group.sort(key=lambda x: (0 if item_body(x) else 1, -recency(x)))
        queues[name] = deque(group[:max_per_collector])

    order = sorted(
        queues,
        key=lambda n: (-(weights or {}).get(n, 1.0), n),
    )
    out: list[Item] = []
    while len(out) < limit and any(queues[n] for n in order):
        progressed = False
        for n in order:
            if len(out) >= limit:
                break
            if queues[n]:
                out.append(queues[n].popleft())
                progressed = True
        if not progressed:
            break
    return out


def collect_subscription_items(
    store: Store,
    *,
    window_hours: int = 48,
    limit: int = 40,
    collector_names: set[str] | None = None,
    unused_only: bool = False,
    max_per_collector: int = 5,
) -> list[Item]:
    """取窗口内 RSS 条目。默认每源最多 5 条、有正文优先、源与源轮询。"""
    names = collector_names if collector_names is not None else _rss_collector_names()
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    items = store.query_items(since=since, unused_only=unused_only, limit=5000)
    items = [it for it in items if it.collector in names]
    weights = _feed_weights() if collector_names is None else None
    return _round_robin(
        items,
        limit=limit,
        max_per_collector=max_per_collector,
        weights=weights,
    )


def _fmt_when(it: Item) -> str:
    t = it.published_at or it.fetched_at
    if not t:
        return "时间未知"
    return t.astimezone(CST).strftime("%Y-%m-%d %H:%M CST")


def render_item_md(it: Item, *, heading_level: int = 2) -> str:
    """单篇离线可读稿。链接只作为附注明文,不充当正文。"""
    hashes = "#" * heading_level
    kind = it.kind.value if isinstance(it.kind, Kind) else str(it.kind)
    body = item_body(it)
    lines = [
        f"{hashes} {it.title}",
        "",
        f"> {it.source.value} · {it.author or it.collector} · {_fmt_when(it)} · `{it.collector}`",
        "",
    ]
    if kind == "video":
        lines.append("**【视频】** 画面印不出来。下面是简介/标题党之外还能读到的文字。")
        lines.append("")
    if body:
        lines.append(body)
        lines.append("")
    else:
        lines.append("_库里没有正文。热榜/视频常如此;文章则多半是抽取失败。_")
        lines.append("")
    if it.url and it.url.startswith("http"):
        lines.append(f"原文地址(需上网,纸上看不到点): `{it.url}`")
        lines.append("")
    return "\n".join(lines)


def render_subscriptions_md(
    items: list[Item],
    *,
    title: str = "订阅更新",
    window_hours: int = 48,
    feeds: list[dict] | None = None,
) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    feed_rows = feeds if feeds is not None else load_feeds()
    with_body = sum(1 for it in items if item_body(it))
    lines = [
        f"# {title}",
        "",
        f"> 统计窗口:过去 {window_hours} 小时 · 生成于 {now}",
        f"> 订阅源 {len(feed_rows)} · 本页 {len(items)} 条 · 其中 {with_body} 条有正文/简介",
        f"> 每源最多 5 条轮询,避免单一榜单占满版面。正文写在下面,不是「打开」链接。",
        "",
    ]
    if feed_rows:
        lines.append("## 订阅清单")
        lines.append("")
        for r in feed_rows:
            w = r.get("weight", "-")
            lines.append(
                f"- **{r['name']}** · `{r.get('source')}` / `{r.get('kind', 'article')}`"
                f" · weight={w}"
            )
        lines.append("")

    if not items:
        lines.append("_窗口内暂无订阅更新(先跑 `uv run main.py collect --only-rss`)。_")
        lines.append("")
        return "\n".join(lines)

    lines.append("## 目录")
    lines.append("")
    for i, it in enumerate(items, start=1):
        flag = "有正文" if item_body(it) else ("视频简介" if it.kind == Kind.VIDEO else "无正文")
        lines.append(f"{i}. {it.title} · {it.source.value} · {flag}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, it in enumerate(items, start=1):
        lines.append(render_item_md(it, heading_level=2).rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_item_files(items: list[Item], dest_dir: Path) -> list[Path]:
    """把每篇写成独立 md,方便离线打开单个文件。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, it in enumerate(items, start=1):
        path = dest_dir / f"{item_slug(it, n=i)}.md"
        path.write_text(render_item_md(it, heading_level=1), encoding="utf-8")
        paths.append(path)
    return paths


def write_subscriptions_section(
    store: Store,
    *,
    window_hours: int = 48,
    limit: int = 40,
    out_dir: Path | None = None,
) -> Path:
    settings = load_settings()
    feeds = load_feeds(rsshub_url=settings.rsshub_url)
    items = collect_subscription_items(
        store, window_hours=window_hours, limit=limit
    )
    md = render_subscriptions_md(items, window_hours=window_hours, feeds=feeds)
    dest_dir = out_dir or settings.render_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "subscriptions.md"
    path.write_text(md, encoding="utf-8")
    return path
