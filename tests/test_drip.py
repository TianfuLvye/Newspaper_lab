"""滴灌游标与口播栏见报。不访问外网。"""
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from core.drip import DripState, DripUnit, advance, load_state, peek, save_state
from core.schema import Item, Kind, Source
from core.store import Store
from core.text import readable_body
from enrich.bilibili import parse_bili_url
from enrich.oral import prepare_oral, units_from_catalog
from enrich.transcript_download import thumbnail_candidates
from pipeline.edition import produce_edition
from pipeline.rank import is_rank_candidate
from render.subscriptions import collect_subscription_items


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def test_drip_peek_advance_resync():
    units = [
        DripUnit("a", "一"),
        DripUnit("b", "二"),
        DripUnit("c", "三"),
    ]
    state = DripState(enabled=True)
    first = peek(units, state)
    check("peek first", first is not None and first.id == "a")
    state = advance(units, state, first)
    check("after a index 1", state.index == 1 and state.last_id == "a")
    check("peek second", peek(units, state).id == "b")

    shuffled = [units[2], units[0], units[1]]
    check("resync after reorder", peek(shuffled, state).id == "b")

    deleted_a = [units[1], units[2]]
    gone = DripState(enabled=True, index=1, last_id="a")
    check("deleted last peeks remaining head", peek(deleted_a, gone).id == "b")

    exhausted = advance(units, state, units[2])
    check("queue end", peek(units, exhausted) is None)

    off = DripState(enabled=False)
    check("disabled", peek(units, off) is None)

    tmp = Path(tempfile.mkdtemp()) / "q.json"
    save_state(tmp, state)
    loaded = load_state(tmp)
    check("persist last_id", loaded.last_id == "a" and loaded.index == 1)


def test_thumbnail_candidates():
    info = {
        "title": "盐",
        "thumbnail": "https://i0.hdslb.com/bfs/archive/cover.jpg@100w.jpg",
        "thumbnails": [
            {"url": "https://i0.hdslb.com/bfs/archive/cover.jpg@320w.jpg"},
            {"url": "https://i0.hdslb.com/bfs/archive/frame.jpg"},
        ],
        "screenshot": "https://i0.hdslb.com/bfs/archive/frame.jpg",
    }
    pics = thumbnail_candidates(info)
    check("two distinct stills", len(pics) == 2, str(pics))
    check("cover first", pics[0]["role"] == "cover")
    keys = {p["url"].split("@", 1)[0] for p in pics}
    check("unique stems", len(keys) == 2, str(keys))


def _video(bvid: str, title: str, content: str, *, collector: str) -> Item:
    return Item(
        source=Source.BILIBILI,
        kind=Kind.VIDEO,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}",
        content=content,
        collector=collector,
        author="硬核料理男子Tiger",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_oral_on_newspaper_advances_only_after_print():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "e.db")
    v1 = _video("BV0000000001", "盐课", "# 盐\n\n盐的用法。", collector="bili_season_546782")
    v2 = _video("BV0000000002", "油课", "# 油\n\n油温。", collector="bili_season_546782")
    store.upsert_items([v1, v2])
    catalog_dir = tmp / "seasons"
    catalog_dir.mkdir()
    payload = {
        "season_id": "546782",
        "videos": [
            {"bvid": "BV0000000001", "title": "盐课", "pic": "https://i0.hdslb.com/bfs/archive/a.jpg"},
            {"bvid": "BV0000000002", "title": "油课", "pic": "https://i0.hdslb.com/bfs/archive/b.jpg"},
        ],
    }
    (catalog_dir / "546782.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    target = parse_bili_url(
        "https://space.bilibili.com/12383027/lists/546782?type=season",
        name="烹饪",
        drip=True,
    )
    drip_dir = tmp / "drip"
    am = produce_edition(
        "am",
        store,
        out_dir=tmp / "am",
        boards=[],
        rss_collectors=set(),
        expected_collectors=[],
        transcribe_oral=False,
        drip_dir=drip_dir,
        catalog_dir=catalog_dir,
        oral_targets=[target],
        mark=True,
    )
    oral_md = (tmp / "am" / "04_oral.md").read_text(encoding="utf-8")
    digest = am.digest_path.read_text(encoding="utf-8")
    check("oral file exists", (tmp / "am" / "04_oral.md").exists())
    check("salt on oral page", "盐的用法" in oral_md)
    check("salt on digest", "盐的用法" in digest)
    check("oil not yet", "油温" not in oral_md)
    check("readable_body for video", "盐的用法" in readable_body(store.find_bilibili_video("BV0000000001")))
    check("still not ranked", is_rank_candidate(store.find_bilibili_video("BV0000000001")) is False)
    state = load_state(drip_dir / "bili.season.546782.json")
    check("cursor advanced after print", state.last_id == "BV0000000001", json.dumps(state.__dict__))

    rss_video = _video("BV999", "塔菲", "一堆相关推荐", collector="rss_b站投稿")
    store.upsert_items([rss_video])
    got = collect_subscription_items(
        store, window_hours=48 * 24, collector_names={"rss_b站投稿"}, unused_only=False
    )
    check("subscribe still drops video", all(it.title != "塔菲" for it in got), str([i.title for i in got]))

    produce_edition(
        "pm",
        store,
        out_dir=tmp / "pm",
        boards=[],
        rss_collectors=set(),
        expected_collectors=[],
        transcribe_oral=False,
        drip_dir=drip_dir,
        catalog_dir=catalog_dir,
        oral_targets=[target],
        mark=True,
    )
    pm_oral = (tmp / "pm" / "04_oral.md").read_text(encoding="utf-8")
    check("pm does not drip", "油温" not in pm_oral and "盐的用法" not in pm_oral)
    state2 = load_state(drip_dir / "bili.season.546782.json")
    check("pm does not advance", state2.last_id == "BV0000000001")

    am2 = produce_edition(
        "am",
        store,
        out_dir=tmp / "am2",
        boards=[],
        rss_collectors=set(),
        expected_collectors=[],
        transcribe_oral=False,
        drip_dir=drip_dir,
        catalog_dir=catalog_dir,
        oral_targets=[target],
        mark=True,
    )
    am2_oral = (tmp / "am2" / "04_oral.md").read_text(encoding="utf-8")
    check("next morning oil", "油温" in am2_oral and "盐的用法" not in am2_oral)
    check("oil on digest", "油温" in am2.digest_path.read_text(encoding="utf-8"))
    store.close()


def test_no_body_does_not_advance():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "e.db")
    empty = Item(
        source=Source.BILIBILI,
        kind=Kind.VIDEO,
        title="空",
        url="https://www.bilibili.com/video/BV0000000001",
        collector="bili_season_546782",
    )
    store.upsert_items([empty])
    catalog_dir = tmp / "seasons"
    catalog_dir.mkdir()
    (catalog_dir / "546782.json").write_text(
        json.dumps({"videos": [{"bvid": "BV0000000001", "title": "空"}]}),
        encoding="utf-8",
    )
    target = parse_bili_url(
        "https://space.bilibili.com/12383027/lists/546782?type=season",
        drip=True,
    )
    drip_dir = tmp / "drip"
    prepared = prepare_oral(
        store,
        "am",
        transcribe=False,
        drip_dir=drip_dir,
        catalog_dir=catalog_dir,
        targets=[target],
    )
    check("no slot without body", prepared.slots == [])
    produce_edition(
        "am",
        store,
        out_dir=tmp / "am",
        boards=[],
        rss_collectors=set(),
        expected_collectors=[],
        transcribe_oral=False,
        drip_dir=drip_dir,
        catalog_dir=catalog_dir,
        oral_targets=[target],
        mark=True,
    )
    check("no drip file if never printed", not (drip_dir / "bili.season.546782.json").exists())
    oral = (tmp / "am" / "04_oral.md").read_text(encoding="utf-8")
    check("placeholder not pretend print", "今日口播未成" in oral)
    store.close()


def test_units_from_catalog():
    units = units_from_catalog({"videos": [{"bvid": "BV1", "title": "甲"}, {"title": "无号"}]})
    check("skip missing bvid", [u.id for u in units] == ["BV1"])


def main() -> int:
    test_drip_peek_advance_resync()
    test_thumbnail_candidates()
    test_oral_on_newspaper_advances_only_after_print()
    test_no_body_does_not_advance()
    test_units_from_catalog()
    print("all drip/oral checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
