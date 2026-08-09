"""采集器注册表 —— 从 config/sources.yaml 实例化热榜网 + 内置 dummy。"""
from __future__ import annotations

from collectors.dummy import DummyCollector
from collectors.hotlist_generic import HotlistCollector
from core.base import BaseCollector
from core.settings import load_hotlist_sources, load_settings


def all_collectors(*, include_dummy: bool = False) -> list[BaseCollector]:
    """返回当前应跑的采集器实例列表。"""
    settings = load_settings()
    out: list[BaseCollector] = []
    if include_dummy:
        out.append(DummyCollector())
    for row in load_hotlist_sources():
        out.append(
            HotlistCollector(
                board=row["board"],
                source=row["source"],
                settings=settings,
            )
        )
    return out


def get_collector(name: str) -> BaseCollector | None:
    """按 name 查找(如 hotlist_weibo / dummy)。"""
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
