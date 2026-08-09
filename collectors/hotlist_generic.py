"""DailyHotApi 热榜采集器 —— Lab 1。

一个类适配 N 个榜单:board 决定请求路径,source 决定 Item.Source。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx

from core.base import BaseCollector
from core.schema import Item, Kind, Source
from core.settings import Settings, load_settings


_HEAT_NUM = re.compile(r"([\d.]+)\s*([万亿])?")


def _to_float(raw) -> float | None:
    """把 '123.4万' / 123456 / None 转成 float。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    m = _HEAT_NUM.fullmatch(s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        n *= 1e4
    elif unit == "亿":
        n *= 1e8
    return n


def _parse_ts(raw) -> datetime | None:
    """接受毫秒/秒时间戳或 ISO 字符串。非法/离谱值返回 None,不炸采集。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        # 微秒时间戳
        if ts > 1e14:
            ts /= 1e6
        elif ts > 1e12:  # 毫秒
            ts /= 1000.0
        # 拒掉明显离谱的年份(1970–2100)
        if ts < 0 or ts > 4102444800:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit():
            return _parse_ts(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.year < 1970 or dt.year > 2100:
            return None
        return dt.astimezone(timezone.utc)
    return None


class HotlistCollector(BaseCollector):
    """一个类适配 N 个榜单 —— 注意这里的抽象层次。"""

    interval_minutes = 30

    def __init__(
        self,
        board: str,
        source: Source | str,
        *,
        settings: Settings | None = None,
    ):
        self.board = board
        self.source = Source(source) if isinstance(source, str) else source
        self.name = f"hotlist_{board}"
        # BaseCollector.board 给 run_collector 写 rank_snapshots 用
        # 与 API board 名保持一致
        self.settings = settings or load_settings()

    def collect(self) -> Iterable[Item]:
        url = f"{self.settings.dailyhot_url}/{self.board}"
        last_err: Exception | None = None
        payload = None
        for attempt in range(2):
            try:
                r = httpx.get(url, timeout=self.settings.dailyhot_timeout)
                r.raise_for_status()
                payload = r.json()
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    continue
                raise
        if payload is None:
            assert last_err is not None
            raise last_err

        rows = payload.get("data") or []
        if not rows:
            # 空结果交给安全壳抬成 EmptyResultError,不要静默 return []
            return

        for i, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if not title:
                continue
            yield Item(
                source=self.source,
                kind=Kind.HOTLIST,
                title=title,
                url=row.get("url") or row.get("mobileUrl") or "",
                summary=row.get("desc"),
                heat=_to_float(row.get("hot")),
                rank=i,
                collector=self.name,
                raw=row,
                published_at=_parse_ts(row.get("timestamp")),
            )
