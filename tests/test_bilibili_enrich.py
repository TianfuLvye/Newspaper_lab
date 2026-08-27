"""B 站白名单发现：解析 URL、合集分页、RSSHub UP 投稿、目录落盘。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from core.schema import Kind, Source
from core.settings import load_bilibili_whitelist
from core.store import Store
from enrich.bilibili import (
    enrich_bilibili,
    fetch_season_videos,
    fetch_up_videos,
    parse_bili_url,
    write_season_catalog,
)


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def test_parse_urls():
    up = parse_bili_url("https://space.bilibili.com/1871365234", name="大问题")
    check("up mid", up.kind == "up" and up.mid == "1871365234")
    season = parse_bili_url(
        "https://space.bilibili.com/12383027/lists/546782?type=season",
        name="烹饪",
    )
    check("season ids", season.kind == "season" and season.mid == "12383027" and season.season_id == "546782")
    old = parse_bili_url(
        "https://space.bilibili.com/12383027/channel/collectiondetail?sid=546782"
    )
    check("old collectiondetail sid", old.season_id == "546782")
    try:
        parse_bili_url("https://space.bilibili.com/1/lists/2?type=series")
        check("series rejected", False)
    except ValueError:
        check("series rejected", True)


def test_load_committed_whitelist():
    cfg = load_bilibili_whitelist()
    check("has example up", any("1871365234" in str(r.get("url")) for r in cfg["ups"]))
    check("has example season", any("546782" in str(r.get("url")) for r in cfg["collections"]))


def test_season_fetch_and_catalog():
    archives = [
        {
            "bvid": f"BV{i:010d}",
            "title": f"课 {i}",
            "duration": 60 + i,
            "pubdate": 1700000000 + i,
            "pic": "",
        }
        for i in range(1, 44)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page_num") or 1)
        start = (page - 1) * 50
        chunk = archives[start : start + 50]
        body = {
            "code": 0,
            "message": "OK",
            "data": {
                "meta": {"name": "合集·测试", "total": 43, "mid": 12383027, "season_id": 546782},
                "page": {"page_num": page, "page_size": 50, "total": 43},
                "archives": chunk,
            },
        }
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    target = parse_bili_url(
        "https://space.bilibili.com/12383027/lists/546782?type=season",
        name="测试合集",
    )
    meta, videos = fetch_season_videos(target, client=client)
    check("season total meta", int(meta["total"]) == 43)
    check("season listed 43", len(videos) == 43, str(len(videos)))
    check("first bvid", videos[0].bvid == "BV0000000001")
    tmp = Path(tempfile.mkdtemp()) / "546782.json"
    write_season_catalog(target, meta, videos, path=tmp)
    saved = json.loads(tmp.read_text(encoding="utf-8"))
    check("catalog drip index", saved["drip"]["index"] == 0)
    check("catalog video count", len(saved["videos"]) == 43)


def test_up_rss_and_upsert():
    rss = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>空间</title>
<item>
  <title>安乐死应该合法化吗</title>
  <link>https://www.bilibili.com/video/BV1in8d6wEXk</link>
  <pubDate>Wed, 27 Aug 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>第二期</title>
  <link>https://www.bilibili.com/video/BV1abcdeFGHI</link>
</item>
</channel></rss>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=rss)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    target = parse_bili_url("https://space.bilibili.com/1871365234", name="大问题")
    videos = fetch_up_videos(target, rsshub_url="http://rss.example", client=client)
    check("up rss two items", len(videos) == 2)
    check("up first bv", videos[0].bvid == "BV1in8d6wEXk")

    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    stats = enrich_bilibili(
        store,
        client=client,
        catalog_dir=Path(tempfile.mkdtemp()),
        targets=[target],
    )
    check("upsert new 2", stats["videos_new"] == 2, str(stats))
    check("ups counted", stats["ups"] == 1)
    items = store.query_items(unused_only=False, kinds=[Kind.VIDEO], sources=[Source.BILIBILI])
    check("stored as video", len(items) == 2 and all(it.kind == Kind.VIDEO for it in items))
    store.close()


if __name__ == "__main__":
    test_parse_urls()
    test_load_committed_whitelist()
    test_season_fetch_and_catalog()
    test_up_rss_and_upsert()
    print("ok")
