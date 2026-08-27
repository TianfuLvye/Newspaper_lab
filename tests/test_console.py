"""订阅控制台：URL 识别、overlay 合并、API CRUD。

运行: uv run python -m tests.test_console
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import yaml
from fastapi.testclient import TestClient

from console.app import create_app
from console.detect import DetectError, detect_input
from console.wewe import WeweClient
from console.yaml_io import FeedPaths, FeedStore
from core.settings import apply_overlay, empty_overlay, load_feeds

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL += 1
        raise AssertionError(name)
    PASS += 1


def test_detect_zhihu_people():
    d = detect_input("https://www.zhihu.com/people/L.M.Sherlock")
    check("people type", d.type == "zhihu")
    check("people answers route", d.url == "{rsshub}/zhihu/people/answers/L.M.Sherlock")
    check("people source", d.source == "zhihu")
    check("not activities", "activities" not in d.url)
    check("name has 回答", "回答" in d.name)


def test_detect_zhihu_org():
    d = detect_input("https://www.zhihu.com/org/zhi-hu-ri-bao-51-41")
    check("org posts route", d.url == "{rsshub}/zhihu/posts/org/zhi-hu-ri-bao-51-41")
    d2 = detect_input("{rsshub}/zhihu/people/answers/chai-ping-jun-80")
    check("rsshub answers passthrough", "chai-ping-jun-80" in d2.url)


def test_detect_wechat_and_rss():
    d = detect_input("https://mp.weixin.qq.com/s/abcdefg123")
    check("wechat needs wewe", d.needs_wewe and d.source == "wechat_mp")
    check("keeps article url", "mp.weixin.qq.com/s/abcdefg123" in (d.wechat_article_url or ""))
    d2 = detect_input("http://127.0.0.1:4000/feeds/MP_WXS_3868095266.atom")
    check("wewe feed id", d2.mp_id == "MP_WXS_3868095266")
    check("wewe no network", d2.needs_wewe is False)
    d3 = detect_input("https://feeds.a.dj.com/rss/RSSWorldNews.xml")
    check("dj is finance", d3.source == "finance" and d3.type == "rss")
    try:
        detect_input("")
        check("empty rejected", False)
    except DetectError:
        check("empty rejected", True)


def test_overlay_merge():
    builtin = [
        {"name": "A", "url": "{rsshub}/a", "source": "zhihu", "kind": "article", "weight": 1.0},
        {"name": "B", "url": "{rsshub}/b", "source": "rss", "kind": "article"},
    ]
    overlay = {
        "feeds": [
            {"name": "C", "url": "https://example.com/c.xml", "source": "rss", "kind": "article"}
        ],
        "replacements": [{"name": "A", "weight": 9.0, "title_regex": "早报"}],
        "disabled": ["B"],
    }
    rows = apply_overlay(builtin, overlay)
    names = [r["name"] for r in rows]
    check("disabled dropped", "B" not in names)
    check("new overlay appended", "C" in names)
    a = next(r for r in rows if r["name"] == "A")
    check("replacement weight", a["weight"] == 9.0)
    check("replacement regex", a["title_regex"] == "早报")
    check("empty overlay is identity", [r["name"] for r in apply_overlay(builtin, empty_overlay())] == ["A", "B"])


def test_load_feeds_tmp_overlay():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sources = root / "sources.yaml"
        overlay = root / "overlay.yaml"
        wechat = root / "wechat.yaml"
        sources.write_text(
            yaml.safe_dump(
                {
                    "feeds": [
                        {
                            "name": "内置甲",
                            "url": "{rsshub}/x",
                            "source": "zhihu",
                            "kind": "article",
                        }
                    ]
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        overlay.write_text(
            yaml.safe_dump(
                {
                    "feeds": [
                        {
                            "name": "自加乙",
                            "url": "https://example.com/feed.xml",
                            "source": "rss",
                            "kind": "article",
                        }
                    ],
                    "replacements": [],
                    "disabled": [],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        wechat.write_text("feeds: []\n", encoding="utf-8")
        feeds = load_feeds(
            sources,
            rsshub_url="http://rsshub.test",
            overlay_path=overlay,
            wechat_path=wechat,
        )
        check("expanded rsshub", feeds[0]["url"] == "http://rsshub.test/x")
        check("two feeds", len(feeds) == 2, str(len(feeds)))


def _fake_wewe_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/platform.getMpInfo"):
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": [
                            {
                                "id": "MP_WXS_1",
                                "name": "差评X.PIN",
                                "cover": "",
                                "intro": "",
                                "updateTime": 1,
                            }
                        ]
                    }
                }
            },
        )
    if path.endswith("/feed.add"):
        return httpx.Response(200, json={"result": {"data": {"json": {"id": "MP_WXS_1"}}}})
    if path.rstrip("/") == "/feeds":
        return httpx.Response(
            200,
            json=[{"id": "MP_WXS_1", "name": "差评X.PIN", "intro": "", "cover": ""}],
        )
    if path.endswith("/feed.list"):
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": {
                            "items": [{"id": "MP_WXS_1", "mpName": "差评X.PIN"}],
                            "nextCursor": None,
                        }
                    }
                }
            },
        )
    return httpx.Response(404, json={"error": {"message": path}})


def _client(td: Path) -> TestClient:
    sources = td / "sources.yaml"
    overlay = td / "overlay.yaml"
    wechat = td / "wechat.yaml"
    sources.write_text(
        yaml.safe_dump(
            {
                "feeds": [
                    {
                        "name": "Thoughts Memo 回答",
                        "url": "{rsshub}/zhihu/people/answers/L.M.Sherlock",
                        "source": "zhihu",
                        "kind": "article",
                        "weight": 2.0,
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    overlay.write_text("feeds: []\nreplacements: []\ndisabled: []\n", encoding="utf-8")
    wechat.write_text("feeds: []\n", encoding="utf-8")
    transport = httpx.MockTransport(_fake_wewe_handler)
    wewe = WeweClient("http://wewe.test", auth_code="secret", transport=transport)
    app = create_app(
        paths=FeedPaths(
            sources=sources,
            overlay=overlay,
            wechat=wechat,
            rsshub_url="http://rsshub.test",
        ),
        db_path=td / "t.db",
        wewe=wewe,
    )
    return TestClient(app)


def test_api_crud():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        client = _client(td)
        listed = client.get("/api/feeds")
        check("list 200", listed.status_code == 200)
        names = {f["name"] for f in listed.json()["feeds"]}
        check("builtin listed", "Thoughts Memo 回答" in names)

        det = client.post(
            "/api/feeds/detect",
            json={"url": "https://www.zhihu.com/people/someone"},
        )
        check("detect 200", det.status_code == 200)
        check("detect answers", "/zhihu/people/answers/someone" in det.json()["url"])

        added = client.post(
            "/api/feeds",
            json={
                "name": "someone 回答",
                "url": "{rsshub}/zhihu/people/answers/someone",
                "source": "zhihu",
                "kind": "article",
                "weight": 1.5,
                "type": "zhihu",
            },
        )
        check("add overlay 200", added.status_code == 200, added.text)
        check("add origin overlay", added.json()["origin"] == "overlay")

        patched = client.patch(
            "/api/feeds/someone 回答",
            json={"weight": 3.3},
        )
        check("patch 200", patched.status_code == 200, patched.text)
        check("patch weight", patched.json()["weight"] == 3.3)

        disabled = client.patch(
            "/api/feeds/Thoughts Memo 回答",
            json={"enabled": False},
        )
        check("disable builtin", disabled.status_code == 200 and disabled.json()["enabled"] is False)

        gone = client.delete("/api/feeds/someone 回答")
        check("delete overlay", gone.status_code == 200 and gone.json()["action"] == "removed")

        wx = client.post(
            "/api/feeds",
            json={
                "name": "微信公众号",
                "url": "",
                "source": "wechat_mp",
                "kind": "article",
                "weight": 2.0,
                "needs_wewe": True,
                "wechat_article_url": "https://mp.weixin.qq.com/s/abcdefg123",
            },
        )
        check("wechat add 200", wx.status_code == 200, wx.text)
        check("wechat origin", wx.json()["origin"] == "wechat")
        check("wechat name from wewe", wx.json()["name"] == "差评X.PIN")
        check("wechat url atom", "MP_WXS_1.atom" in wx.json()["url"])

        imported = client.post("/api/wewe/import", json={"ids": ["MP_WXS_1"]})
        check("import skip duplicate", imported.status_code == 200)
        check("import skipped", len(imported.json()["skipped"]) >= 1, json.dumps(imported.json()))

        xml = """<?xml version="1.0"?><rss version="2.0"><channel>
        <title>t</title><item><title>Hello</title><link>https://example.com/1</link></item>
        </channel></rss>"""
        feed_path = td / "local.xml"
        feed_path.write_text(xml, encoding="utf-8")
        store = FeedStore(
            FeedPaths(
                sources=td / "sources.yaml",
                overlay=td / "overlay.yaml",
                wechat=td / "wechat.yaml",
                rsshub_url="http://rsshub.test",
            )
        )
        store.add(
            {
                "name": "本地 RSS",
                "url": feed_path.as_uri(),
                "source": "rss",
                "kind": "article",
            },
            origin="overlay",
        )
        collected = client.post("/api/feeds/本地 RSS/collect")
        check("collect 200", collected.status_code == 200, collected.text)
        check("collect new", collected.json()["new"] >= 1, str(collected.json()))


def main():
    test_detect_zhihu_people()
    test_detect_zhihu_org()
    test_detect_wechat_and_rss()
    test_overlay_merge()
    test_load_feeds_tmp_overlay()
    test_api_crud()
    print(f"All console checks passed ({PASS} assertions).")


if __name__ == "__main__":
    main()
