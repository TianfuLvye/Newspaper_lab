"""采集器注册表 —— 从 config/sources.yaml 实例化热榜网 + RSS 订阅 + 内置 dummy。"""
from __future__ import annotations

from collectors.dummy import DummyCollector
from collectors.hotlist_generic import HotlistCollector
from collectors.rss_generic import RSSCollector
from core.base import BaseCollector
from core.settings import load_feeds, load_hotlist_sources, load_settings


def all_collectors(
    *,
    include_dummy: bool = False,
    include_hotlist: bool = True,
    include_rss: bool = True,
) -> list[BaseCollector]:
    """返回当前应跑的采集器实例列表。"""
    settings = load_settings()
    out: list[BaseCollector] = []
    if include_dummy:
        out.append(DummyCollector())
    if include_hotlist:
        for row in load_hotlist_sources():
            out.append(
                HotlistCollector(
                    board=row["board"],
                    source=row["source"],
                    settings=settings,
                )
            )
    if include_rss:
        for row in load_feeds(rsshub_url=settings.rsshub_url):
            out.append(RSSCollector(row))
    return out


def get_collector(name: str) -> BaseCollector | None:
    """按 name 查找(如 hotlist_weibo / rss_泛式_投稿 / dummy)。"""
    if name == "dummy":
        return DummyCollector()
    for c in all_collectors(include_dummy=False):
        if c.name == name:
            return c
    return None


def list_collector_names(*, include_dummy: bool = True) -> list[str]:
    names = [c.name for c in all_collectors(include_dummy=False)]
    if include_dummy:
        names = ["dummy", *names]
    return names
