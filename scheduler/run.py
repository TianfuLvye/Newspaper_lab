"""Lab 6 · 常驻调度。

两种节奏:
  撒网  热榜 30min / RSS 60min / 定向 6h / 正文抽取 6h
  收网  07:00 早报 / 19:00 晚报;成功后立刻 push 邮件

jitter + coalesce 是新手最常漏的两个参数:
  没有 jitter,所有采集器整点齐发,像一次小型 DDoS;
  没有 coalesce,机器休眠一晚醒来会瞬间补跑几十个任务。
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.base import BaseCollector, run_collector
from core.registry import all_collectors, get_collector
from core.settings import Settings, load_settings
from core.store import Store
from pipeline.edition import produce_edition

log = logging.getLogger("fishnet.scheduler")

# 出报 07:00 / 19:00。开刷落在前两小时窗口的前一小时，给顺序拉号留余量。
WEWE_REFRESH_JITTER_SECONDS = 3600
WEWE_REFRESH_GAP = (45.0, 90.0)


def scheduled_collectors(*, include_targeted: bool = True) -> list[BaseCollector]:
    """调度器要挂的网:热榜 + RSS + (可选)已启用的定向采集。不含 dummy。"""
    return [
        c
        for c in all_collectors(
            include_dummy=False,
            include_hotlist=True,
            include_rss=True,
            include_targeted=include_targeted,
        )
        if c.enabled
    ]


def _job_collect(name: str, db_path: Path) -> None:
    """一张网一次。异常由 run_collector 吞掉,绝不向上抛。"""
    store = Store(db_path)
    try:
        c = get_collector(name)
        if c is None:
            log.error("未知 collector: %s", name)
            return
        if not c.enabled:
            log.info("跳过未启用的 %s", name)
            return
        new, dup = run_collector(c, store)
        log.info("[%s] new=%d dup=%d", name, new, dup)
    except Exception:
        log.exception("[%s] scheduler shell failed", name)
    finally:
        store.close()


def _job_enrich(db_path: Path, limit: int) -> None:
    from enrich.bilibili import enrich_bilibili
    from enrich.extract import enrich_store

    store = Store(db_path)
    try:
        bili = enrich_bilibili(store)
        log.info("bili whitelist %s", bili)
        stats = enrich_store(store, limit=limit)
        log.info("enrich %s", stats)
    except Exception:
        log.exception("enrich failed")
    finally:
        store.close()


def wechat_feeds_for_refresh(feeds: list[dict] | None = None) -> list[tuple[str, str]]:
    """从 load_feeds() 抽出有 MP_WXS_ 的公众号 (name, mp_id)。"""
    from console.wewe import mp_id_from_url
    from core.settings import load_feeds

    rows = feeds if feeds is not None else load_feeds()
    out: list[tuple[str, str]] = []
    for row in rows:
        if str(row.get("source") or "") != "wechat_mp":
            continue
        mp_id = mp_id_from_url(str(row.get("url") or ""))
        if mp_id:
            out.append((str(row.get("name") or mp_id), mp_id))
    return out


def refresh_wechat_feeds(
    *,
    client=None,
    feeds: list[dict] | None = None,
    delay_range: tuple[float, float] = WEWE_REFRESH_GAP,
    sleep_fn=time.sleep,
    shuffle: bool = True,
) -> dict:
    """请 WeWe 按随机顺序刷公众号。单号失败继续下一个。不碰微信本身。"""
    from console.wewe import WeweClient
    from core.settings import load_env_file, load_settings

    rows = wechat_feeds_for_refresh(feeds)
    if shuffle:
        random.shuffle(rows)
    own_client = client is None
    if client is None:
        load_env_file()
        client = WeweClient(load_settings().wewe_url)
    ok: list[str] = []
    failed: list[str] = []
    try:
        for i, (name, mp_id) in enumerate(rows):
            try:
                log.info("wewe refresh start name=%s mp=%s", name, mp_id)
                client.refresh_articles(mp_id)
                ok.append(name)
            except Exception:
                log.exception("wewe refresh failed name=%s", name)
                failed.append(name)
            if i < len(rows) - 1:
                lo, hi = delay_range
                gap = random.uniform(lo, hi) if hi > lo else lo
                if gap > 0:
                    sleep_fn(gap)
    finally:
        if own_client:
            client.close()
    return {"ok": ok, "failed": failed, "order": [n for n, _ in rows]}


def _job_wewe_refresh() -> None:
    try:
        stats = refresh_wechat_feeds()
        log.info("wewe refresh done %s", stats)
    except Exception:
        log.exception("wewe refresh job failed")


def _job_push(edition_dir: Path) -> None:
    """出报成功后发邮件。SMTP 没配或已发过都跳过;失败不让出报任务崩。"""
    from core.settings import load_env_file
    from notify.config import load_notify_config
    from notify.push import push_edition_dir

    try:
        load_env_file()
        cfg = load_notify_config()
        result = push_edition_dir(edition_dir, config=cfg)
        log.info(
            "push %s edition=%s to=%s reason=%s",
            result.status,
            result.edition_id,
            ",".join(result.to) or "-",
            result.reason or "-",
        )
    except Exception:
        log.exception("push failed dir=%s", edition_dir)


def _job_edition(kind: str, db_path: Path) -> None:
    store = Store(db_path)
    try:
        result = produce_edition(kind, store)
        log.info(
            "edition %s status=%s digest=%s failures=%s",
            result.edition_id,
            result.status,
            result.digest_path,
            result.failures,
        )
        if result.status != "failed" and result.digest_path is not None:
            _job_push(result.digest_path.parent)
    except Exception:
        log.exception("edition %s crashed", kind)
    finally:
        store.close()


def build_scheduler(
    *,
    db_path: Path | None = None,
    settings: Settings | None = None,
    collectors: list[BaseCollector] | None = None,
    include_targeted: bool = True,
    jitter: int | None = None,
) -> BlockingScheduler:
    """组装调度器,不 start。测试只检查 job 配置。"""
    cfg = settings or load_settings()
    db = Path(db_path) if db_path is not None else cfg.db_path
    tz = ZoneInfo(cfg.scheduler_tz)
    jitter_s = cfg.scheduler_jitter_seconds if jitter is None else jitter
    nets = collectors if collectors is not None else scheduled_collectors(
        include_targeted=include_targeted
    )

    sched = BlockingScheduler(timezone=tz)
    for c in nets:
        if not c.enabled:
            continue
        sched.add_job(
            _job_collect,
            IntervalTrigger(minutes=c.interval_minutes, jitter=jitter_s, timezone=tz),
            kwargs={"name": c.name, "db_path": db},
            id=c.name,
            name=c.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(300, c.interval_minutes * 60 // 2),
            replace_existing=True,
        )

    sched.add_job(
        _job_enrich,
        IntervalTrigger(hours=cfg.enrich_interval_hours, jitter=jitter_s, timezone=tz),
        kwargs={"db_path": db, "limit": cfg.enrich_limit},
        id="enrich",
        name="enrich",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        replace_existing=True,
    )
    sched.add_job(
        _job_edition,
        CronTrigger(
            hour=cfg.edition_am_hour,
            minute=cfg.edition_am_minute,
            timezone=tz,
        ),
        kwargs={"kind": "am", "db_path": db},
        id="edition_am",
        name="edition_am",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        replace_existing=True,
    )
    sched.add_job(
        _job_edition,
        CronTrigger(
            hour=cfg.edition_pm_hour,
            minute=cfg.edition_pm_minute,
            timezone=tz,
        ),
        kwargs={"kind": "pm", "db_path": db},
        id="edition_pm",
        name="edition_pm",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        replace_existing=True,
    )
    for job_id, hour in (("wewe_refresh_am", 5), ("wewe_refresh_pm", 16)):
        sched.add_job(
            _job_wewe_refresh,
            CronTrigger(
                hour=hour,
                minute=0,
                jitter=WEWE_REFRESH_JITTER_SECONDS,
                timezone=tz,
            ),
            id=job_id,
            name=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=WEWE_REFRESH_JITTER_SECONDS,
            replace_existing=True,
        )
    return sched


def serve(
    *,
    db_path: Path | None = None,
    include_targeted: bool = True,
) -> int:
    """阻塞运行。Ctrl-C 退出。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    sched = build_scheduler(db_path=db_path, include_targeted=include_targeted)
    jobs = ", ".join(sorted(j.id for j in sched.get_jobs()))
    log.info("scheduler starting jobs=%s db=%s", jobs, db_path or load_settings().db_path)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")
        return 0
    return 0
