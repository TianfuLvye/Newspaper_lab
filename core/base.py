"""Collector 基类与安全壳 —— Lab 0 / Lab 6 标准答案。"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable

from core.schema import Item
from core.store import Store

log = logging.getLogger("fishnet")


class BaseCollector(ABC):
    """一张渔网。

    契约(违反了整个系统会烂掉):
      - collect() 只负责抓取和转换,禁止去重、禁止过滤、禁止调 LLM
      - 抓不到就抛异常,**绝对不要 return []**
      - 不要自己写库,由 runner 统一负责
    """
    name: str = "unnamed"
    interval_minutes: int = 60
    enabled: bool = True
    # 该 collector 对应的热榜 board 名,非热榜留 None
    board: str | None = None

    @abstractmethod
    def collect(self) -> Iterable[Item]:
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name} every {self.interval_minutes}m>"


class EmptyResultError(RuntimeError):
    """抓到 0 条。

    单独成类是因为它几乎总是「页面改版 / 被限流」的信号,
    而不是「今天真的没有新内容」。必须能被告警系统识别出来。
    """


def run_collector(c: BaseCollector, store: Store, *, max_seconds: int = 300,
                  min_expected: int = 1) -> tuple[int, int]:
    """安全壳:捕获一切异常,记录运行状态,永不向上抛。

    这是 Lab 6 「失败隔离」原则的落点——一张网破了,别的网继续捞。
    """
    run_id = store.start_run(c.name)
    t0 = time.monotonic()
    try:
        items = []
        for it in c.collect():
            items.append(it)
            if time.monotonic() - t0 > max_seconds:
                log.warning("[%s] 超时截断于 %d 条", c.name, len(items))
                break
        if len(items) < min_expected:
            raise EmptyResultError(f"{c.name} 仅返回 {len(items)} 条")

        new, dup = store.upsert_items(items)
        if c.board:
            store.record_snapshot(items, c.board)
        store.finish_run(run_id, "ok", len(items), new)
        log.info("[%s] ok total=%d new=%d dup=%d", c.name, len(items), new, dup)
        return new, dup
    except Exception as e:
        store.finish_run(run_id, "failed", 0, 0, error=repr(e)[:500])
        log.exception("[%s] failed", c.name)
        return 0, 0
