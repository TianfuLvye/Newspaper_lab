"""Lab 1:产出「今日新上榜 Top 20」Markdown 片段。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.schema import Item
from core.settings import load_settings
from core.store import Store

CST = timezone(timedelta(hours=8))


def _fmt_heat(h: float | None) -> str:
    if h is None:
        return "-"
    if h >= 1e8:
        return f"{h / 1e8:.1f}亿"
    if h >= 1e4:
        return f"{h / 1e4:.1f}万"
    return f"{h:.0f}"


def collect_newly_entered_items(
    store: Store,
    boards: list[str],
    *,
    window_hours: int = 6,
    limit: int = 20,
) -> list[Item]:
    """合并多榜 newly_entered,按 heat/rank 粗排取 Top N。"""
    seen: set[str] = set()
    items: list[Item] = []
    for board in boards:
        for h in store.newly_entered(board, window_hours=window_hours):
            if h in seen:
                continue
            seen.add(h)
            it = store.get_item(h)
            if it is not None:
                items.append(it)
    items.sort(
        key=lambda x: (
            -(x.heat or 0.0),
            x.rank if x.rank is not None else 999,
        )
    )
    return items[:limit]


def render_hotlist_md(
    items: list[Item],
    *,
    title: str = "今日新上榜 Top 20",
    window_hours: int = 6,
) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    lines = [
        f"# {title}",
        "",
        f"> 统计窗口:过去 {window_hours} 小时首次上榜 · 生成于 {now}",
        f"> 说明:以下为平台热度排序候选,不代表重要性。",
        "",
    ]
    if not items:
        lines.append("_窗口内暂无新上榜条目(需要至少两次采样才能区分「新」与「一直在」)。_")
        lines.append("")
        return "\n".join(lines)

    lines.append("| # | 来源 | 标题 | 热度 | 链接 |")
    lines.append("|---|---|---|---|---|")
    for i, it in enumerate(items, start=1):
        title_cell = it.title.replace("|", "\\|")
        url = it.url or ""
        link = f"[打开]({url})" if url else "-"
        lines.append(
            f"| {i} | {it.source.value} | {title_cell} | {_fmt_heat(it.heat)} | {link} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_hotlist_section(
    store: Store,
    boards: list[str],
    *,
    out_path: Path | None = None,
    window_hours: int = 6,
    limit: int = 20,
) -> Path:
    settings = load_settings()
    path = out_path or (settings.render_dir / "hotlist.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    items = collect_newly_entered_items(
        store, boards, window_hours=window_hours, limit=limit
    )
    path.write_text(
        render_hotlist_md(items, window_hours=window_hours),
        encoding="utf-8",
    )
    return path
