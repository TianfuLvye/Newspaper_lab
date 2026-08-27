"""口播栏 Markdown。合集滴灌与订阅 UP 的见报稿。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.schema import Item
from render.subscriptions import render_item_md

CST = timezone(timedelta(hours=8))
ORAL_MAX_IMAGES = 2


def render_oral_md(
    items: list[Item],
    *,
    title: str = "口播",
    images=None,
    notes: list[str] | None = None,
) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    lines = [
        f"# {title}",
        "",
        f"> 合集按天滴灌，订阅 UP 新片转写后见报。不进头版打分。 · {now}",
        "",
    ]
    if notes:
        for note in notes:
            lines.append(f"> {note}")
        lines.append("")
    if not items:
        lines.append("_今日口播未成。_")
        lines.append("")
        return "\n".join(lines)
    for it in items:
        lines.append(
            render_item_md(
                it,
                heading_level=2,
                images=images,
                max_images=ORAL_MAX_IMAGES,
            ).rstrip()
        )
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
