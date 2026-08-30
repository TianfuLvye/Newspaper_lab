"""Lab 6 验收:调度配置、失败隔离出报、used_in、体检页能报出制造的故障。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("FISHNET_SKIP_LAYOUT", "1")
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from collectors.targeted_xhs import XHSCreatorCollector
from core.base import BaseCollector
from core.schema import Item, Kind, Source
from core.store import Store
from main import build_parser, main
from pipeline.edition import produce_edition
from pipeline.health import diagnose
from render.health import render_health_md
from render.subscriptions import collect_subscription_items
from scheduler.run import build_scheduler, refresh_wechat_feeds

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "lab-06-scheduler.md"
ADR = ROOT / "docs" / "adr" / "005-scheduler-runtime.md"


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def _insert_run(
    store: Store,
    collector: str,
    *,
    status: str,
    new_count: int,
    started_at: datetime,
    error: str | None = None,
) -> None:
    iso = started_at.astimezone(timezone.utc).isoformat()
    store._conn.execute(
        "INSERT INTO collector_runs "
        "(collector, started_at, finished_at, status, item_count, new_count, error) "
        "VALUES (?,?,?,?,?,?,?)",
        (collector, iso, iso, status, new_count, new_count, error),
    )


class _FakeNet(BaseCollector):
    name = "hotlist_weibo"
    interval_minutes = 30

    def collect(self):
        yield Item(
            Source.WEIBO,
            Kind.HOTLIST,
            "占位",
            "https://weibo.com/x",
            collector=self.name,
        )


class _DisabledNet(BaseCollector):
    name = "xhs_skip"
    interval_minutes = 360
    enabled = False

    def collect(self):
        raise RuntimeError("should not be scheduled")


def test_scheduler_job_config():
    tmp = Path(tempfile.mkdtemp()) / "s.db"
    sched = build_scheduler(
        db_path=tmp,
        collectors=[_FakeNet(), _DisabledNet()],
        jitter=120,
    )
    jobs = {j.id: j for j in sched.get_jobs()}
    check("has edition_am", "edition_am" in jobs)
    check("has edition_pm", "edition_pm" in jobs)
    check("has enrich", "enrich" in jobs)
    check("has hotlist job", "hotlist_weibo" in jobs)
    check("disabled not scheduled", "xhs_skip" not in jobs)
    check("dummy not scheduled", "dummy" not in jobs)

    job = jobs["hotlist_weibo"]
    check("coalesce", job.coalesce is True)
    check("max_instances=1", job.max_instances == 1)
    trig = job.trigger
    check("interval trigger", isinstance(trig, IntervalTrigger))
    check("interval 30min", trig.interval == timedelta(minutes=30), str(trig.interval))
    check("jitter 120", trig.jitter == 120, str(trig.jitter))

    am = jobs["edition_am"].trigger
    pm = jobs["edition_pm"].trigger
    check("am is cron", isinstance(am, CronTrigger))
    check("pm is cron", isinstance(pm, CronTrigger))
    check("am 07:00", "hour='7'" in str(am) and "minute='0'" in str(am), str(am))
    check("pm 19:00", "hour='19'" in str(pm) and "minute='0'" in str(pm), str(pm))

    enrich = jobs["enrich"].trigger
    check("enrich interval 6h", enrich.interval == timedelta(hours=6), str(enrich.interval))

    check("has wewe_refresh_am", "wewe_refresh_am" in jobs)
    check("has wewe_refresh_pm", "wewe_refresh_pm" in jobs)
    wewe_am = jobs["wewe_refresh_am"].trigger
    wewe_pm = jobs["wewe_refresh_pm"].trigger
    check("wewe am is cron", isinstance(wewe_am, CronTrigger))
    check("wewe pm is cron", isinstance(wewe_pm, CronTrigger))
    check(
        "wewe am 05:00",
        "hour='5'" in str(wewe_am) and "minute='0'" in str(wewe_am),
        str(wewe_am),
    )
    check(
        "wewe pm 16:00",
        "hour='16'" in str(wewe_pm) and "minute='0'" in str(wewe_pm),
        str(wewe_pm),
    )
    check("wewe jitter 3600", wewe_am.jitter == 3600, str(wewe_am.jitter))
    check("wewe coalesce", jobs["wewe_refresh_am"].coalesce is True)


class _FakeWewe:
    def __init__(self):
        self.calls: list[str] = []

    def refresh_articles(self, mp_id: str) -> None:
        self.calls.append(mp_id)
        if mp_id == "MP_WXS_22":
            raise RuntimeError("boom")


def test_refresh_wechat_skips_non_wechat():
    fake = _FakeWewe()
    slept: list[float] = []
    feeds = [
        {"name": "甲", "url": "http://wewe.test/feeds/MP_WXS_11.atom", "source": "wechat_mp"},
        {"name": "知乎", "url": "{rsshub}/zhihu/people/answers/x", "source": "zhihu"},
        {"name": "乙", "url": "http://wewe.test/feeds/MP_WXS_22.atom", "source": "wechat_mp"},
        {"name": "丙", "url": "http://wewe.test/feeds/MP_WXS_33.atom", "source": "wechat_mp"},
        {"name": "华尔街", "url": "https://feeds.a.dj.com/rss/x.xml", "source": "finance"},
    ]
    stats = refresh_wechat_feeds(
        client=fake,
        feeds=feeds,
        delay_range=(1.0, 1.0),
        sleep_fn=slept.append,
        shuffle=False,
    )
    check("only wechat refreshed", fake.calls == ["MP_WXS_11", "MP_WXS_22", "MP_WXS_33"], str(fake.calls))
    check("ok names", stats["ok"] == ["甲", "丙"], str(stats))
    check("failed isolated", stats["failed"] == ["乙"], str(stats))
    check("slept between accounts", slept == [1.0, 1.0], str(slept))


def test_health_reports_manufactured_failure():
    tmp = Path(tempfile.mkdtemp()) / "h.db"
    store = Store(tmp)
    now = datetime.now(timezone.utc)
    _insert_run(
        store,
        "rss_broken",
        status="failed",
        new_count=0,
        started_at=now - timedelta(hours=2),
        error="RuntimeError('RSSHub down')",
    )
    _insert_run(
        store,
        "hotlist_ok",
        status="ok",
        new_count=12,
        started_at=now - timedelta(hours=1),
    )
    report = diagnose(store, expected=["rss_broken", "hotlist_ok", "never_ran"])
    check("not ok", report.ok is False)
    check("broken in never_ok", "rss_broken" in report.never_ok, str(report.never_ok))
    check("never_ran is idle", "never_ran" in report.idle, str(report.idle))
    check("never_ran not failed", "never_ran" not in report.never_ok)
    check("healthy not flagged", "hotlist_ok" not in report.never_ok and "hotlist_ok" not in report.idle)
    md = render_health_md(report)
    check("md title", "# 系统体检" in md)
    check("md names failure", "rss_broken" in md and "RSSHub down" in md)
    check("md names missing", "never_ran" in md)
    store.close()


def test_health_volume_drop():
    tmp = Path(tempfile.mkdtemp()) / "v.db"
    store = Store(tmp)
    now = datetime.now(timezone.utc)
    for day in range(2, 8):
        _insert_run(
            store,
            "hotlist_weibo",
            status="ok",
            new_count=100,
            started_at=now - timedelta(days=day, hours=1),
        )
    _insert_run(
        store,
        "hotlist_weibo",
        status="ok",
        new_count=1,
        started_at=now - timedelta(hours=3),
    )
    report = diagnose(store, expected=["hotlist_weibo"])
    row = next(c for c in report.collectors if c.name == "hotlist_weibo")
    check("volume_drop flagged", row.volume_drop, f"24h={row.new_24h} mean={row.daily_mean_7d:.1f}")
    check("not never_ok", row.never_ok_24h is False)
    store.close()


def _seed_board(store: Store, n: int = 3) -> list[Item]:
    items = [
        Item(
            Source.WEIBO,
            Kind.HOTLIST,
            f"热搜{i}",
            f"https://weibo.com/lab6/{i}",
            rank=i + 1,
            heat=1000.0 * (n - i),
            collector="hotlist_weibo",
        )
        for i in range(n)
    ]
    store.upsert_items(items)
    store.record_snapshot(items, "weibo")
    rss = Item(
        Source.ZHIHU,
        Kind.ARTICLE,
        "订阅长文",
        "https://zhuanlan.zhihu.com/p/lab6",
        collector="rss_lab6",
        summary="摘要",
        content="这是一篇可以印在纸上的完整正文。",
    )
    store.upsert_items([rss])
    return items + [rss]


def test_edition_used_in_not_repeated():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "e.db")
    items = _seed_board(store)
    hashes = {it.content_hash for it in items}

    am = produce_edition(
        "am",
        store,
        out_dir=tmp / "am",
        boards=["weibo"],
        rss_collectors={"rss_lab6"},
        expected_collectors=[],
    )
    check("am wrote digest", am.digest_path.exists())
    check("am marked items", hashes <= set(am.used_hashes), str(am.used_hashes))
    leftover = store.query_items(unused_only=True)
    check("unused empty after am", leftover == [], str(leftover))

    pm = produce_edition(
        "pm",
        store,
        out_dir=tmp / "pm",
        boards=["weibo"],
        rss_collectors={"rss_lab6"},
        expected_collectors=[],
    )
    check("pm does not reuse hashes", set(pm.used_hashes).isdisjoint(hashes), str(pm.used_hashes))
    check("different edition ids", am.edition_id.endswith("-am") and pm.edition_id.endswith("-pm"))
    am_text = am.digest_path.read_text(encoding="utf-8")
    check("am digest has hotlist title", "热搜0" in am_text)
    check("am digest has article body", "可以印在纸上的完整正文" in am_text)
    check("am digest is not click table", "[打开]" not in am_text)
    item_files = list((tmp / "am" / "items").glob("*.md"))
    check("wrote offline item md", len(item_files) >= 1, str(item_files))
    pm_text = pm.digest_path.read_text(encoding="utf-8")
    check("pm digest omits morning items", "热搜0" not in pm_text and "订阅长文" not in pm_text)
    store.close()


def test_edition_isolates_section_failure():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "iso.db")
    _seed_board(store)

    import pipeline.edition as ed

    orig = ed._run_hotlist

    def boom(store, boards, skip=None):
        raise RuntimeError("DailyHotApi 挂了")

    ed._run_hotlist = boom
    try:
        result = produce_edition(
            "am",
            store,
            out_dir=tmp / "iso",
            boards=["weibo"],
            rss_collectors={"rss_lab6"},
            expected_collectors=["rss_broken"],
        )
    finally:
        ed._run_hotlist = orig

    check("still wrote digest", result.digest_path.exists())
    check("status not total fail", result.status in ("ok", "partial"), result.status)
    check("hotlist listed in failures", any(n == "hotlist" for n, _ in result.failures), str(result.failures))
    digest = result.digest_path.read_text(encoding="utf-8")
    check("placeholder in digest", "本栏目今日无数据" in digest)
    check("subscriptions survived", "订阅长文" in digest)
    check("health page present", "系统体检" in digest)
    store.close()


def test_edition_health_page_shows_fault():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "fault.db")
    _insert_run(
        store,
        "rss_broken",
        status="failed",
        new_count=0,
        started_at=datetime.now(timezone.utc),
        error="ConnectionError('rsshub:1200')",
    )
    result = produce_edition(
        "pm",
        store,
        out_dir=tmp / "fault",
        boards=[],
        rss_collectors=set(),
        expected_collectors=["rss_broken"],
    )
    health_md = (result.digest_path.parent / "99_health.md").read_text(encoding="utf-8")
    check("health file exists", "rss_broken" in health_md)
    check("error text in health", "rsshub:1200" in health_md)
    check("digest contains health", "rss_broken" in result.digest_path.read_text(encoding="utf-8"))
    store.close()


def test_cli_subcommands_independent():
    parser = build_parser()
    help_text = parser.format_help()
    for cmd in ("collect", "stats", "render", "push", "enrich", "health", "serve"):
        check(f"help lists {cmd}", cmd in help_text)

    tmp = Path(tempfile.mkdtemp()) / "cli.db"
    Store(tmp).close()
    rc_stats = main(["--db", str(tmp), "stats"])
    check("stats runnable", rc_stats == 0, str(rc_stats))
    rc_health = main(["--db", str(tmp), "health"])
    check("health runnable", rc_health in (0, 1), str(rc_health))
    rc_ed = main(
        ["--db", str(tmp), "render", "--edition", "am", "--out-dir", str(tmp.parent / "ed")]
    )
    check("render --edition runnable", rc_ed in (0, 1), str(rc_ed))
    rc_push = main(["--db", str(tmp), "push"])
    check("push runnable", rc_push == 0, str(rc_push))


def test_targeted_disabled_without_creator():
    c = XHSCreatorCollector({"name": "空", "creator_id": ""})
    check("no creator_id → disabled", c.enabled is False)


def test_round_robin_caps_one_feed():
    tmp = Path(tempfile.mkdtemp()) / "rr.db"
    store = Store(tmp)
    now = datetime.now(timezone.utc)
    items = []
    for i in range(20):
        items.append(
            Item(
                Source.BILIBILI,
                Kind.VIDEO,
                f"周榜{i}",
                f"https://bilibili.com/v/{i}",
                collector="rss_spam",
                fetched_at=now,
            )
        )
    for i in range(3):
        items.append(
            Item(
                Source.FINANCE,
                Kind.ARTICLE,
                f"财经{i}",
                f"https://wallstreetcn.com/a/{i}",
                collector="rss_finance",
                content="财经正文" * 20,
                fetched_at=now,
            )
        )
    store.upsert_items(items)
    picked = collect_subscription_items(
        store,
        window_hours=24,
        limit=8,
        collector_names={"rss_spam", "rss_finance"},
        max_per_collector=3,
    )
    by: dict[str, int] = {}
    for it in picked:
        by[it.collector] = by.get(it.collector, 0) + 1
    check("spam capped", by.get("rss_spam", 0) <= 3, str(by))
    check("finance present", by.get("rss_finance", 0) >= 1, str(by))
    store.close()


def test_docs():
    check("lab-06 doc exists", DOC.exists())
    text = DOC.read_text(encoding="utf-8")
    for key in ("APScheduler", "jitter", "coalesce", "used_in", "系统体检"):
        check(f"doc mentions {key}", key in text)
    check("adr-005 exists", ADR.exists())
    adr = ADR.read_text(encoding="utf-8")
    check("adr mentions 家庭 IP", "家庭" in adr or "树莓派" in adr)


def main_tests() -> None:
    test_scheduler_job_config()
    test_refresh_wechat_skips_non_wechat()
    test_health_reports_manufactured_failure()
    test_health_volume_drop()
    test_edition_used_in_not_repeated()
    test_edition_isolates_section_failure()
    test_edition_health_page_shows_fault()
    test_cli_subcommands_independent()
    test_targeted_disabled_without_creator()
    test_round_robin_caps_one_feed()
    test_docs()
    print("All Lab 6 checks passed.")


if __name__ == "__main__":
    main_tests()
