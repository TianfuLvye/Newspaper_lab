"""Lab 3:产出「订阅更新」Markdown 片段。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.schema import Item
from core.settings import load_feeds, load_settings
from core.store import Store

CST = timezone(timedelta(hours=8))


def _rss_collector_names(feeds: list[dict] | None = None) -> set[str]:
    from collectors.rss_generic import slugify

    rows = feeds if feeds is not None else load_feeds()
    return {f"rss_{slugify(r['name'])}" for r in rows}


def collect_subscription_items(
    store: Store,
    *,
    window_hours: int = 48,
    limit: int = 40,
    collector_names: set[str] | None = None,
) -> list[Item]:
    """取窗口内由 RSS collectors 写入的条目,按 published_at / fetched_at 倒序。"""
    names = collector_names if collector_names is not None else _rss_collector_names()
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    # query_items 默认 unused_only;订阅版面要能反复渲染,关掉该过滤
    items = store.query_items(since=since, unused_only=False, limit=5000)
    items = [it for it in items if it.collector in names]
    items.sort(
        key=lambda x: (
            x.published_at or x.fetched_at or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    return items[:limit]


def render_subscriptions_md(
    items: list[Item],
    *,
    title: str = "订阅更新",
    window_hours: int = 48,
    feeds: list[dict] | None = None,
) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    feed_rows = feeds if feeds is not None else load_feeds()
    lines = [
        f"# {title}",
        "",
        f"> 统计窗口:过去 {window_hours} 小时 · 生成于 {now}",
        f"> 订阅源数:{len(feed_rows)} · 本页展示 {len(items)} 条",
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

    lines.append("## 最近更新")
    lines.append("")
    if not items:
        lines.append("_窗口内暂无订阅更新(先跑 `uv run main.py collect --only-rss`)。_")
        lines.append("")
        return "\n".join(lines)

    lines.append("| # | 来源 | 订阅网 | 标题 | 作者 | 发布时间 | 链接 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, it in enumerate(items, start=1):
        title_cell = (it.title or "").replace("|", "\\|")
        author = (it.author or "-").replace("|", "\\|")
        if it.published_at:
            pub = it.published_at.astimezone(CST).strftime("%m-%d %H:%M")
        else:
            pub = "-"
        url = it.url or ""
        link = f"[打开]({url})" if url.startswith("http") else "-"
        lines.append(
            f"| {i} | {it.source.value} | {it.collector} | {title_cell} "
            f"| {author} | {pub} | {link} |"
        )
    lines.append("")
    return "\n".join(lines)


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
