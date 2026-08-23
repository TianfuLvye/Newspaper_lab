"""Lab 4 验收:进程隔离、频率/并发、Item 转换入库、笔记与 ADR。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from collectors.targeted_xhs import XHSCreatorCollector, row_to_item
from core.registry import all_collectors, get_collector
from core.schema import Kind, Source
from core.store import Store

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "xhs_creator.jsonl"
ADR = ROOT / "docs" / "adr" / "003-mediacrawler-scope.md"
NOTES = ROOT / "docs" / "notes" / "anti-crawling.md"


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def test_interval_and_concurrency():
    c = XHSCreatorCollector(
        {"name": "fixture", "creator_id": "x", "max_concurrency": 1},
        jsonl_path=FIXTURE,
    )
    check("interval ≥ 6h", c.interval_minutes >= 360, str(c.interval_minutes))
    check("concurrency == 1", c.max_concurrency == 1)
    try:
        XHSCreatorCollector({"name": "bad", "max_concurrency": 10}, jsonl_path=FIXTURE)
        check("reject concurrency>1", False)
    except ValueError:
        check("reject concurrency>1", True)


def test_jsonl_to_item_and_store():
    c = XHSCreatorCollector(
        {"name": "示例创作者", "creator_id": "fixture"},
        jsonl_path=FIXTURE,
    )
    items = list(c.collect())
    check("fixture yields ≥1", len(items) >= 2, str(len(items)))
    it = items[0]
    check("source=xiaohongshu", it.source == Source.XHS, it.source.value)
    check("kind=post", it.kind == Kind.POST, it.kind.value)
    check("has url", it.url.startswith("https://"), it.url)
    check("has author", bool(it.author), str(it.author))
    check("collector prefix", it.collector.startswith("xhs_"), it.collector)

    tmp = Path(tempfile.mkdtemp()) / "lab4.db"
    store = Store(tmp)
    new, dup = store.upsert_items(items)
    check("first upsert all new", new == len(items) and dup == 0, f"new={new} dup={dup}")
    new2, dup2 = store.upsert_items(items)
    check("second upsert all dup", new2 == 0 and dup2 == len(items), f"new={new2} dup={dup2}")
    stats = store.stats()
    check(
        "store has xiaohongshu",
        stats.get("by_source", {}).get("xiaohongshu", 0) == len(items),
        str(stats.get("by_source")),
    )
    store.close()


def test_empty_jsonl_raises():
    empty = Path(tempfile.mkdtemp()) / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    c = XHSCreatorCollector({"name": "empty", "creator_id": "x"}, jsonl_path=empty)
    try:
        list(c.collect())
        check("empty jsonl raises", False)
    except RuntimeError as e:
        check("empty jsonl raises", "无有效笔记" in str(e), str(e)[:80])


def test_row_to_item_raw_note_shape():
    row = {
        "note_id": "abc",
        "title": "原始结构",
        "desc": "hello",
        "time": 1722000000000,
        "user": {"nickname": "U", "user_id": "uid1"},
        "interact_info": {"liked_count": "9"},
    }
    it = row_to_item(row, collector="xhs_t")
    check("raw note maps", it is not None and it.author == "U" and it.heat == 9.0)


def test_not_in_default_collect():
    names = {c.name for c in all_collectors(include_dummy=False)}
    targeted = {c.name for c in all_collectors(include_dummy=False, include_targeted=True)}
    check("default collect 不含 xhs_", not any(n.startswith("xhs_") for n in names), str(names))
    check("include_targeted 含配置项", any(n.startswith("xhs_") for n in targeted), str(targeted))
    got = get_collector("xhs_小红书示例创作者")
    check("get_collector 能找到 targeted", got is not None and got.interval_minutes >= 360)


def test_docs():
    check("ADR-003 exists", ADR.exists())
    text = ADR.read_text(encoding="utf-8")
    check("ADR 写清定位", "小红书" in text and ("6 小时" in text or "360" in text))
    check("ADR 写清边界", "搜索" in text or "RSSHub" in text)
    check("anti-crawling.md exists", NOTES.exists())
    notes = NOTES.read_text(encoding="utf-8")
    for key in ("登录", "签名", "Playwright", "并发"):
        check(f"notes mentions {key}", key in notes)


def main():
    test_interval_and_concurrency()
    test_jsonl_to_item_and_store()
    test_empty_jsonl_raises()
    test_row_to_item_raw_note_shape()
    test_not_in_default_collect()
    test_docs()
    print("All Lab 4 checks passed.")


if __name__ == "__main__":
    main()
