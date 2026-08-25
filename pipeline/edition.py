"""Lab 6 · 收网:出一期早报 / 晚报。

每个版面独立生成,单版失败写成「本栏目今日无数据」,绝不阻断出报。
成功写入 digest 后才 mark used_in,保证早报出现过的内容不进晚报。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.settings import load_hotlist_sources, load_settings
from core.store import Store
from pipeline.health import HealthReport, diagnose
from render.health import render_health_md
from render.hotlist import collect_newly_entered_items, render_hotlist_md
from render.subscriptions import (
    collect_subscription_items,
    render_subscriptions_md,
    write_item_files,
)

log = logging.getLogger("fishnet.edition")
CST = timezone(timedelta(hours=8))

PLACEHOLDER = "_本栏目今日无数据。_"


@dataclass
class SectionResult:
    name: str
    filename: str
    markdown: str
    hashes: list[str] = field(default_factory=list)
    error: str | None = None
    has_content: bool = False
    items: list = field(default_factory=list)


@dataclass
class EditionResult:
    edition_id: str
    kind: str
    digest_path: Path
    sections: list[SectionResult]
    used_hashes: list[str]
    health: HealthReport | None
    failures: list[tuple[str, str]]
    status: str  # ok / partial / failed


def edition_id_for(kind: str, *, now: datetime | None = None) -> str:
    if kind not in ("am", "pm"):
        raise ValueError(f"edition 必须是 am 或 pm,得到 {kind!r}")
    ts = (now or datetime.now(timezone.utc)).astimezone(CST)
    return f"{ts:%Y-%m-%d}-{kind}"


def _placeholder(name: str, title: str, error: str) -> SectionResult:
    md = f"# {title}\n\n{PLACEHOLDER}\n\n> 原因: `{error}`\n"
    return SectionResult(
        name=name,
        filename="",
        markdown=md,
        error=error,
        has_content=False,
    )


def _run_hotlist(store: Store, boards: list[str]) -> SectionResult:
    items = collect_newly_entered_items(
        store, boards, window_hours=6, limit=20, unused_only=True
    )
    md = render_hotlist_md(items, window_hours=6)
    return SectionResult(
        name="hotlist",
        filename="02_hotlist.md",
        markdown=md,
        hashes=[it.content_hash for it in items],
        has_content=bool(items),
    )


def _run_subscriptions(store: Store, collector_names: set[str] | None) -> SectionResult:
    items = collect_subscription_items(
        store,
        window_hours=168,
        limit=24,
        collector_names=collector_names,
        unused_only=True,
        max_per_collector=4,
    )
    md = render_subscriptions_md(items, window_hours=168)
    return SectionResult(
        name="subscriptions",
        filename="06_subscribe.md",
        markdown=md,
        hashes=[it.content_hash for it in items],
        has_content=bool(items),
        items=items,
    )


def _run_health(
    store: Store, expected: list[str] | None
) -> tuple[SectionResult, HealthReport]:
    report = diagnose(store, expected=expected)
    md = render_health_md(report)
    return (
        SectionResult(
            name="health",
            filename="99_health.md",
            markdown=md,
            has_content=True,
        ),
        report,
    )


def _merge_digest(
    *,
    edition_id: str,
    kind: str,
    sections: list[SectionResult],
    failures: list[tuple[str, str]],
) -> str:
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    label = "早报" if kind == "am" else "晚报"
    lines = [
        f"# 渔网{label} · {edition_id}",
        "",
        f"> 期号 `{edition_id}` · 生成于 {ts}",
        f"> 版面 {len(sections)} · 失败 {len(failures)}",
        "",
    ]
    if failures:
        lines.append("**本期残缺。** 失败版面:")
        lines.append("")
        for name, err in failures:
            lines.append(f"- `{name}`: {err}")
        lines.append("")
        lines.append("---")
        lines.append("")
    for sec in sections:
        lines.append(sec.markdown.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
    if lines[-2] == "---":
        lines = lines[:-2]
    return "\n".join(lines).rstrip() + "\n"


def produce_edition(
    kind: str,
    store: Store,
    *,
    now: datetime | None = None,
    out_dir: Path | None = None,
    boards: list[str] | None = None,
    rss_collectors: set[str] | None = None,
    expected_collectors: list[str] | None = None,
    section_timeout: float = 180,
    deadline_minutes: float = 20,
    mark: bool = True,
) -> EditionResult:
    """出一期报纸。只要有一个内容版面成功(或体检页写出)就落盘。

    `deadline_minutes` 是硬约束:超时的版面改占位,体检页尽量保留。
    """
    eid = edition_id_for(kind, now=now)
    settings = load_settings()
    dest = out_dir or (settings.editions_dir / eid)
    dest.mkdir(parents=True, exist_ok=True)
    board_names = boards if boards is not None else [r["board"] for r in load_hotlist_sources()]

    run_id = store.start_run(f"edition_{kind}")
    t0 = time.monotonic()
    deadline = deadline_minutes * 60
    failures: list[tuple[str, str]] = []
    sections: list[SectionResult] = []
    health_report: HealthReport | None = None

    content_jobs = [
        ("hotlist", "热榜速览", lambda: _run_hotlist(store, board_names)),
        ("subscriptions", "订阅更新", lambda: _run_subscriptions(store, rss_collectors)),
    ]

    for name, title, fn in content_jobs:
        elapsed = time.monotonic() - t0
        remaining = deadline - elapsed
        if remaining <= 0:
            err = f"总时限 {deadline_minutes:.0f}min 已到,跳过"
            failures.append((name, err))
            sec = _placeholder(name, title, err)
            sec.filename = "02_hotlist.md" if name == "hotlist" else "06_subscribe.md"
            sections.append(sec)
            continue
        try:
            t_sec = time.monotonic()
            sec = fn()
            took = time.monotonic() - t_sec
            if took > section_timeout:
                log.warning(
                    "[%s] section %s took %.1fs (budget %.0fs)",
                    eid, name, took, section_timeout,
                )
        except Exception as e:
            err = repr(e)
            log.warning("[%s] section %s failed: %s", eid, name, err)
            failures.append((name, err))
            sec = _placeholder(name, title, err)
            sec.filename = "02_hotlist.md" if name == "hotlist" else "06_subscribe.md"
        sections.append(sec)

    try:
        health_sec, health_report = _run_health(store, expected_collectors)
        sections.append(health_sec)
    except Exception as e:
        err = repr(e)
        log.exception("[%s] health failed", eid)
        failures.append(("health", err))
        sec = _placeholder("health", "系统体检", err)
        sec.filename = "99_health.md"
        sections.append(sec)

    digest = _merge_digest(
        edition_id=eid, kind=kind, sections=sections, failures=failures
    )
    digest_path = dest / "digest.md"
    digest_path.write_text(digest, encoding="utf-8")
    for sec in sections:
        if sec.filename:
            (dest / sec.filename).write_text(sec.markdown, encoding="utf-8")
        if sec.name == "subscriptions" and sec.items:
            write_item_files(sec.items, dest / "items")

    used: list[str] = []
    seen: set[str] = set()
    for sec in sections:
        for h in sec.hashes:
            if h not in seen:
                seen.add(h)
                used.append(h)
    if mark and used:
        store.mark_used(used, eid)

    has_content = any(s.has_content and s.name != "health" for s in sections)
    if has_content and not failures:
        status = "ok"
    elif has_content or health_report is not None:
        status = "partial" if failures else "ok"
    else:
        status = "failed"

    store.finish_run(
        run_id,
        status,
        item_count=len(used),
        new_count=len(used),
        error="; ".join(f"{n}: {e}" for n, e in failures)[:500] or None,
    )
    log.info(
        "[%s] %s sections=%d used=%d failures=%d path=%s",
        eid,
        status,
        len(sections),
        len(used),
        len(failures),
        digest_path,
    )
    return EditionResult(
        edition_id=eid,
        kind=kind,
        digest_path=digest_path,
        sections=sections,
        used_hashes=used,
        health=health_report,
        failures=failures,
        status=status,
    )
