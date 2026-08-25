"""Lab 6:报纸最后一页「系统体检」。"""
from __future__ import annotations

from datetime import timedelta, timezone

from pipeline.health import HealthReport

CST = timezone(timedelta(hours=8))


def _fmt_bytes(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f} KB"
    return f"{n} B"


def render_health_md(report: HealthReport) -> str:
    now = report.generated_at.astimezone(CST).strftime("%Y-%m-%d %H:%M CST")
    lines = [
        "# 系统体检",
        "",
        f"> 生成于 {now}",
        "",
    ]
    if report.ok:
        lines.append("**全部正常。** 已配置的采集器在 24h 内有成功记录,产出无明显骤降。")
        lines.append("")
    else:
        lines.append(f"**告警 {len(report.alerts)} 项** —— 读报时请扫一眼。")
        lines.append("")
        for a in report.alerts:
            lines.append(f"- {a}")
        lines.append("")

    if report.never_ok:
        lines.append("## 采集失败(24h 跑过但全挂)")
        lines.append("")
        lines.append("这些网今天有跑、但一次都没成功。常见原因:上游挂了、页面改版、Cookie 过期。")
        lines.append("")
        for name in report.never_ok:
            row = next(c for c in report.collectors if c.name == name)
            err = row.last_error or "(无错误文本)"
            lines.append(f"- `{name}` · runs={row.runs_24h} failed={row.failed_24h} · {err}")
        lines.append("")

    if report.idle:
        lines.append("## 调度未跑(24h 0 次)")
        lines.append("")
        lines.append(
            "不是网破了,是常驻进程没在采。开 `uv run main.py serve`,或手动 "
            "`uv run main.py collect`。"
        )
        lines.append("")
        for name in report.idle:
            row = next(c for c in report.collectors if c.name == name)
            last = (row.last_ok or "-")[:19]
            lines.append(f"- `{name}` · 上次成功 {last}")
        lines.append("")

    if report.volume_drops:
        lines.append("## 产出骤降(低于 7 日均值 20%)")
        lines.append("")
        for name in report.volume_drops:
            row = next(c for c in report.collectors if c.name == name)
            lines.append(
                f"- `{name}`: 24h new={row.new_24h} · 7日日均 {row.daily_mean_7d:.1f}"
            )
        lines.append("")

    lines.append("## 容量")
    lines.append("")
    oldest = (
        f"{report.oldest_unused_hours:.1f}h"
        if report.oldest_unused_hours is not None
        else "无"
    )
    unread_age = (
        f"{report.oldest_unread_hours:.1f}h"
        if report.oldest_unread_hours is not None
        else "无"
    )
    lines.append(f"- 数据库: {_fmt_bytes(report.db_bytes)}")
    lines.append(f"- 未上报纸条目: {report.unused_count} · 最老 {oldest}(含热榜标题)")
    lines.append(f"- 有正文未读: {report.unread_articles} · 最老 {unread_age}")
    lines.append("")

    lines.append("## 各网 24h")
    lines.append("")
    if not report.collectors:
        lines.append("_尚无 collector_runs 记录。_")
        lines.append("")
        return "\n".join(lines)

    lines.append("| collector | runs | ok | fail | new | last_ok |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for c in report.collectors:
        mark = ""
        if c.never_ok_24h:
            mark = " **"
        elif c.idle_24h:
            mark = " ·"
        elif c.volume_drop:
            mark = " *"
        last_ok = (c.last_ok or "-")[:19]
        lines.append(
            f"| `{c.name}`{mark} | {c.runs_24h} | {c.ok_24h} | "
            f"{c.failed_24h} | {c.new_24h} | {last_ok} |"
        )
    lines.append("")
    lines.append("_`**` 全失败 · `·` 未调度 · `*` 产出骤降_")
    lines.append("")
    return "\n".join(lines)
