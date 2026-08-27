"""通用滴灌游标：按序每次取一条，成功见报后再前进。

不依赖 Item / B 站 / STT。合集、以后的书章节都给 list[DripUnit] 即可。
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class DripUnit:
    id: str
    title: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class DripState:
    enabled: bool = True
    index: int = 0
    order: str = "collection"
    last_id: str | None = None
    last_at: str | None = None


def queue_filename(queue_id: str) -> str:
    token = _SAFE_ID.sub("_", (queue_id or "").strip()) or "queue"
    return f"{token}.json"


def drip_path(root: Path, queue_id: str) -> Path:
    return Path(root) / queue_filename(queue_id)


def load_state(path: Path, *, enabled: bool = True) -> DripState:
    if not path.exists():
        return DripState(enabled=enabled)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DripState(enabled=enabled)
    if not isinstance(raw, dict):
        return DripState(enabled=enabled)
    return DripState(
        enabled=bool(raw.get("enabled", enabled)),
        index=max(0, int(raw.get("index") or 0)),
        order=str(raw.get("order") or "collection"),
        last_id=(str(raw["last_id"]) if raw.get("last_id") else None),
        last_at=(str(raw["last_at"]) if raw.get("last_at") else None),
    )


def save_state(path: Path, state: DripState) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _index_of(units: Sequence[DripUnit], unit_id: str | None) -> int | None:
    if not unit_id:
        return None
    for i, unit in enumerate(units):
        if unit.id == unit_id:
            return i
    return None


def next_index(units: Sequence[DripUnit], state: DripState) -> int:
    """下一条在 units 里的下标；队列耗尽则为 len(units)。"""
    if not units:
        return 0
    found = _index_of(units, state.last_id)
    if found is not None:
        return found + 1
    if state.last_id:
        return 0
    return min(max(0, state.index), len(units))


def peek(units: Sequence[DripUnit], state: DripState) -> DripUnit | None:
    if not state.enabled:
        return None
    idx = next_index(units, state)
    if idx < 0 or idx >= len(units):
        return None
    return units[idx]


def advance(
    units: Sequence[DripUnit],
    state: DripState,
    unit: DripUnit,
    *,
    now: datetime | None = None,
) -> DripState:
    """见报（或确认失效跳过）后前进。last_id 以本次 unit 为准。"""
    ts = (now or datetime.now(UTC)).astimezone(UTC)
    found = _index_of(units, unit.id)
    idx = (found + 1) if found is not None else next_index(units, state) + 1
    return DripState(
        enabled=state.enabled,
        index=max(0, idx),
        order=state.order,
        last_id=unit.id,
        last_at=ts.isoformat(),
    )
