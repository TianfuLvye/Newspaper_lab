"""Lab 0 假采集器:不访问网络,固定产出一条 Item,用来跑通入库与幂等。"""
from __future__ import annotations

from collections.abc import Iterable

from core.base import BaseCollector
from core.schema import Item, Kind, Source


class DummyCollector(BaseCollector):
    name = "dummy"
    interval_minutes = 60

    def collect(self) -> Iterable[Item]:
        yield Item(
            source=Source.OTHER,
            kind=Kind.ARTICLE,
            title="Hello Fishnet",
            url="https://example.com/?utm_source=x",
            collector=self.name,
            raw={"note": "lab0-dummy"},
        )
