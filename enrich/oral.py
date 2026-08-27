"""口播出报准备：合集滴灌 + UP 新片。游标只在见报后前进。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.drip import DripUnit, advance, drip_path, load_state, peek, save_state
from core.schema import Item, Kind
from core.settings import load_settings
from core.store import Store
from core.text import item_published_at
from enrich.bilibili import catalog_path, load_targets

log = logging.getLogger("fishnet.oral")

UP_WINDOW_HOURS = 48
MAX_SKIP_GONE = 5

_GONE_MARKERS = (
    "video unavailable",
    "has been deleted",
    "http error 404",
    "is not available",
    "removed by the uploader",
)


@dataclass
class OralSlot:
    item: Item
    source: str  # drip | up
    queue_id: str | None = None
    unit: DripUnit | None = None


@dataclass
class PreparedOral:
    slots: list[OralSlot] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    drip_dir: Path | None = None
    catalog_dir: Path | None = None


def default_drip_dir() -> Path:
    return load_settings().db_path.parent / "drip"


def default_catalog_dir() -> Path:
    return catalog_path("x").parent


def season_queue_id(season_id: str) -> str:
    return f"bili.season.{season_id}"


def units_from_catalog(payload: dict) -> list[DripUnit]:
    units: list[DripUnit] = []
    for row in payload.get("videos") or []:
        if not isinstance(row, dict):
            continue
        bvid = str(row.get("bvid") or "").strip()
        if not bvid:
            continue
        units.append(
            DripUnit(id=bvid, title=str(row.get("title") or ""), extra=dict(row))
        )
    return units


def load_catalog(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def is_gone_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _GONE_MARKERS)


def merge_item_images(store: Store, item: Item, extra: list[dict]) -> Item:
    """把封面候选并进 Item.images，去重 URL。"""
    have = {(rec.get("url") or "") for rec in (item.images or []) if rec.get("url")}
    merged = list(item.images or [])
    for rec in extra:
        url = rec.get("url") if isinstance(rec, dict) else None
        if not url or url in have:
            continue
        merged.append(dict(rec))
        have.add(url)
    if merged != list(item.images or []):
        store.update_images(item.content_hash, merged)
        fresh = store.get_item(item.content_hash)
        if fresh is not None:
            return fresh
        item.images = merged
    return item


def _cover_from_unit(unit: DripUnit) -> list[dict]:
    pic = unit.extra.get("pic") if unit.extra else None
    if not pic:
        return []
    return [{"url": str(pic), "alt": unit.title, "role": "cover"}]


def _reload(store: Store, item: Item) -> Item:
    fresh = store.get_item(item.content_hash)
    return fresh if fresh is not None else item


def _has_body(item: Item) -> bool:
    return bool((item.content or "").strip())


def _transcribe(store: Store, item: Item) -> Item:
    from enrich.transcript import parse_bvid, run_transcript

    bvid = parse_bvid(item.url or "")
    run_transcript(bvid, store=store)
    found = store.find_bilibili_video(bvid)
    return found if found is not None else _reload(store, item)


def prepare_drip_slots(
    store: Store,
    *,
    transcribe: bool,
    drip_dir: Path,
    catalog_dir: Path,
    targets: list | None = None,
) -> tuple[list[OralSlot], list[str]]:
    slots: list[OralSlot] = []
    notes: list[str] = []
    wanted = targets if targets is not None else load_targets()
    for target in wanted:
        if target.kind != "season" or not target.season_id or not target.drip:
            continue
        cat = load_catalog(catalog_dir / f"{target.season_id}.json")
        units = units_from_catalog(cat)
        if not units:
            notes.append(f"合集 {target.season_id} 无目录")
            continue
        qid = season_queue_id(target.season_id)
        state = load_state(drip_path(drip_dir, qid), enabled=True)
        state.enabled = True
        skipped = 0
        while skipped < MAX_SKIP_GONE:
            unit = peek(units, state)
            if unit is None:
                notes.append(f"合集 {target.name or target.season_id} 滴灌已尽")
                break
            item = store.find_bilibili_video(unit.id)
            if item is None:
                notes.append(f"{unit.id} 未入库，等 enrich")
                break
            item = merge_item_images(store, item, _cover_from_unit(unit))
            if item.used_in:
                state = advance(units, state, unit)
                save_state(drip_path(drip_dir, qid), state)
                skipped += 1
                notes.append(f"{unit.id} 已见报 {item.used_in}，跳过")
                continue
            if not _has_body(item):
                if not transcribe:
                    notes.append(f"{unit.id} 尚无正文")
                    break
                try:
                    item = _transcribe(store, item)
                except Exception as exc:  # noqa: BLE001
                    if is_gone_error(exc):
                        state = advance(units, state, unit)
                        save_state(drip_path(drip_dir, qid), state)
                        skipped += 1
                        notes.append(f"{unit.id} 已失效，跳过")
                        log.warning("drip skip gone %s: %s", unit.id, exc)
                        continue
                    notes.append(f"{unit.id} 转写失败，下期重试")
                    log.warning("drip transcript failed %s: %s", unit.id, exc)
                    break
            item = _reload(store, item)
            if not _has_body(item):
                notes.append(f"{unit.id} 转写后仍无正文")
                break
            slots.append(
                OralSlot(item=item, source="drip", queue_id=qid, unit=unit)
            )
            break
    return slots, notes


def prepare_up_slots(
    store: Store,
    *,
    transcribe: bool,
    window_hours: int = UP_WINDOW_HOURS,
) -> tuple[list[OralSlot], list[str]]:
    notes: list[str] = []
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)
    items = store.query_items(kinds=[Kind.VIDEO], unused_only=True, limit=5000)
    grouped: dict[str, list[Item]] = {}
    for it in items:
        collector = it.collector or ""
        if not collector.startswith("bili_up_"):
            continue
        when = item_published_at(it)
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when < cutoff:
                continue
        grouped.setdefault(collector, []).append(it)

    slots: list[OralSlot] = []
    for collector, group in grouped.items():
        group.sort(
            key=lambda it: item_published_at(it) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        chosen = next((it for it in group if _has_body(it)), None)
        if chosen is None and transcribe and group:
            try:
                chosen = _transcribe(store, group[0])
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{collector} 转写失败: {exc}")
                log.warning("up transcript failed %s: %s", collector, exc)
                continue
        if chosen is None or not _has_body(chosen):
            continue
        slots.append(OralSlot(item=chosen, source="up"))
    return slots, notes


def prepare_oral(
    store: Store,
    kind: str,
    *,
    transcribe: bool = True,
    drip_dir: Path | None = None,
    catalog_dir: Path | None = None,
    targets: list | None = None,
) -> PreparedOral:
    drip_root = Path(drip_dir) if drip_dir is not None else default_drip_dir()
    cat_root = Path(catalog_dir) if catalog_dir is not None else default_catalog_dir()
    prepared = PreparedOral(drip_dir=drip_root, catalog_dir=cat_root)
    if kind == "am":
        slots, notes = prepare_drip_slots(
            store,
            transcribe=transcribe,
            drip_dir=drip_root,
            catalog_dir=cat_root,
            targets=targets,
        )
        prepared.slots.extend(slots)
        prepared.notes.extend(notes)
    up_slots, up_notes = prepare_up_slots(store, transcribe=transcribe)
    prepared.slots.extend(up_slots)
    prepared.notes.extend(up_notes)
    return prepared


def commit_drip(prepared: PreparedOral, printed: list[Item], *, now: datetime | None = None) -> None:
    """仅对已经出现在口播栏里的滴灌稿前进游标。"""
    if prepared.drip_dir is None or prepared.catalog_dir is None:
        return
    printed_hashes = {it.content_hash for it in printed}
    for slot in prepared.slots:
        if slot.source != "drip" or not slot.queue_id or slot.unit is None:
            continue
        if slot.item.content_hash not in printed_hashes:
            continue
        season_id = slot.queue_id.rsplit(".", 1)[-1]
        cat = load_catalog(prepared.catalog_dir / f"{season_id}.json")
        units = units_from_catalog(cat)
        path = drip_path(prepared.drip_dir, slot.queue_id)
        state = load_state(path, enabled=True)
        state.enabled = True
        save_state(path, advance(units, state, slot.unit, now=now))
