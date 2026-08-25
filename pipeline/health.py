"""Lab 6 · 系统体检。

出报时扫一遍采集健康度,印在报纸最后一页。写进报纸而不是日志:
每天读报时顺便完成运维巡检。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.registry import all_collectors
from core.store import Store

# 产出量低于 7 日日均的这个比例 → 告警(手册:低 80% 以上,即剩余 < 20%)
VOLUME_DROP_RATIO = 0.20
VOLUME_BASELINE_MIN = 3.0


@dataclass
class CollectorHealth:
    name: str
    runs_24h: int = 0
    ok_24h: int = 0
    failed_24h: int = 0
    new_24h: int = 0
    new_7d: int = 0
    last_run: str | None = None
    last_ok: str | None = None
    last_error: str | None = None
    idle_24h: bool = False
    never_ok_24h: bool = False
    volume_drop: bool = False

    @property
    def daily_mean_7d(self) -> float:
        return self.new_7d / 7.0

    @property
    def severity(self) -> str:
        if self.never_ok_24h:
            return "fail"
        if self.idle_24h or self.volume_drop:
            return "warn"
        return "ok"


@dataclass
class HealthReport:
    generated_at: datetime
    collectors: list[CollectorHealth]
    db_bytes: int
    unused_count: int
    oldest_unused_hours: float | None
    unread_articles: int = 0
    oldest_unread_hours: float | None = None
    alerts: list[str] = field(default_factory=list)

    @property
    def never_ok(self) -> list[str]:
        return [c.name for c in self.collectors if c.never_ok_24h]

    @property
    def idle(self) -> list[str]:
        return [c.name for c in self.collectors if c.idle_24h]

    @property
    def volume_drops(self) -> list[str]:
        return [c.name for c in self.collectors if c.volume_drop]

    @property
    def ok(self) -> bool:
        return not self.alerts


def _as_int(v) -> int:
    if v is None:
        return 0
    return int(v)


def diagnose(
    store: Store,
    *,
    expected: list[str] | None = None,
) -> HealthReport:
    """对照预期 collector 名单。

    「24h 没跑过」和「跑过但全失败」要分开:前者常常只是没开 serve,
    后者才是网破了。
    """
    names = expected
    if names is None:
        names = [
            c.name
            for c in all_collectors(
                include_dummy=False,
                include_hotlist=True,
                include_rss=True,
                include_targeted=True,
            )
            if c.enabled
        ]
    h24 = {r["collector"]: r for r in store.health(hours=24)}
    h7 = {r["collector"]: r for r in store.health(hours=168)}
    # 只体检当前配置的网。已下线的源(如曾订阅的每周必看)留在 runs 表里,不再报警。
    seen = set(names)

    collectors: list[CollectorHealth] = []
    alerts: list[str] = []
    idle_names: list[str] = []
    for name in sorted(seen):
        a = h24.get(name, {})
        b = h7.get(name, {})
        row = CollectorHealth(
            name=name,
            runs_24h=_as_int(a.get("runs")),
            ok_24h=_as_int(a.get("ok_runs")),
            failed_24h=_as_int(a.get("failed_runs")),
            new_24h=_as_int(a.get("new_items")),
            new_7d=_as_int(b.get("new_items")),
            last_run=a.get("last_run") or b.get("last_run"),
            last_ok=a.get("last_ok") or b.get("last_ok"),
            last_error=a.get("last_error"),
        )
        if name in names and row.runs_24h == 0:
            row.idle_24h = True
            idle_names.append(name)
        elif name in names and row.ok_24h == 0:
            row.never_ok_24h = True
            err = f" · {row.last_error}" if row.last_error else ""
            alerts.append(f"{name} 过去 24h 全失败{err}")
        elif (
            row.daily_mean_7d >= VOLUME_BASELINE_MIN
            and row.new_24h < VOLUME_DROP_RATIO * row.daily_mean_7d
        ):
            row.volume_drop = True
            alerts.append(
                f"{name} 产出骤降:24h={row.new_24h} "
                f"< 20% × 7日日均 {row.daily_mean_7d:.1f}"
            )
        collectors.append(row)

    if idle_names:
        alerts.append(
            f"{len(idle_names)} 张网 24h 未调度(没开 `uv run main.py serve`?)"
        )

    unused_count, oldest = store.unused_age()
    unread, unread_age = store.unused_readable_age()
    db_bytes = store.db_size_bytes()
    if unread_age is not None and unread_age >= 72 and unread >= 5:
        alerts.append(
            f"{unread} 篇有正文的未读已积压 {unread_age:.0f}h,该出报了"
        )

    return HealthReport(
        generated_at=datetime.now(timezone.utc),
        collectors=collectors,
        db_bytes=db_bytes,
        unused_count=unused_count,
        oldest_unused_hours=oldest,
        unread_articles=unread,
        oldest_unread_hours=unread_age,
        alerts=alerts,
    )
