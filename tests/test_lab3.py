"""Lab 3 验收:RSSCollector / feeds 配置 / subscriptions 渲染。

网络相关用例在本机 RSSHub 未启动时 skip,不拖垮 CI/离线开发。
"""
from __future__ import annotations

import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collectors.rss_generic import (
    RSSCollector,
    _extract_content,
    _parse_struct_time,
    _strip_html,
    slugify,
)
from core.registry import all_collectors, get_collector, list_collector_names
from core.schema import Item, Kind, Source
from core.settings import load_feeds, load_settings
from core.store import Store
from render.subscriptions import (
    collect_subscription_items,
    render_subscriptions_md,
    write_subscriptions_section,
)


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def _rsshub_up() -> bool:
    url = load_settings().rsshub_url.rstrip("/") + "/"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def test_slugify_and_helpers():
    check("slugify ascii", slugify("Hello World") == "hello_world")
    check("slugify zh", slugify("泛式 投稿").startswith("泛式"))
    check("strip_html keeps paragraphs", _strip_html("<p>a<br/>b</p>") == "a\n\nb")
    check("strip_html title is single line", _strip_html("<p>a<br/>b</p>", single_line=True) == "a b")
    entry = {
        "content": [{"value": "<p>" + ("长正文" * 40) + "</p>"}],
        "summary": "短",
    }
    body = _extract_content(entry)
    check("extract_content prefers content", body is not None and len(body) > 80)
    st = (2024, 1, 2, 3, 4, 5, 0, 0, 0)
    dt = _parse_struct_time(st)
    check("parse_struct_time utc", dt is not None and dt.tzinfo is not None)
    check("parse_struct_time none", _parse_struct_time(None) is None)


def test_feeds_config_coverage():
    feeds = load_feeds()
    check("feeds >= 10", len(feeds) >= 10, f"got {len(feeds)}")
    names = {f["name"] for f in feeds}
    # 用户指定账号必须出现
    must_substrings = ["Thoughts Memo", "差评君", "知乎日报", "泛式", "好柿花生", "华尔街日报"]
    for s in must_substrings:
        check(f"feed covers {s}", any(s in n for n in names))
    # 验收:至少 2 个 B 站 UP + 至少 1 个知乎 + 新番相关
    bili_video = [
        f for f in feeds if f.get("source") == "bilibili" and "user/video" in f["url"]
    ]
    zhihu = [f for f in feeds if f.get("source") == "zhihu"]
    bangumi_ish = [
        f
        for f in feeds
        if "bangumi" in f["url"] or "weekly" in f["url"] or "新番" in f["name"] or "放送" in f["name"]
    ]
    check("≥2 bilibili UP video feeds", len(bili_video) >= 2, str(len(bili_video)))
    daily = next(f for f in feeds if "知乎日报" in f["name"])
    check("zaobao title_regex set", bool(daily.get("title_regex")))
    check(
        "zaobao uses org posts route",
        "posts/org/zhi-hu-ri-bao-51-41" in daily["url"],
        daily["url"],
    )
    check("has bangumi/weekly style feed", len(bangumi_ish) >= 1)
    check("does not subscribe 每周必看", not any("每周必看" in n for n in names))
    # {rsshub} 应被展开
    check(
        "rsshub placeholder expanded",
        all("{rsshub}" not in f["url"] for f in feeds),
    )
    base = load_settings().rsshub_url
    check(
        "urls point at configured rsshub or absolute http",
        all(f["url"].startswith("http") for f in feeds),
        base,
    )


def test_registry_rss_collectors():
    rss = all_collectors(include_hotlist=False, include_rss=True)
    check("registry builds rss collectors", len(rss) >= 10, str(len(rss)))
    check("names start with rss_", all(c.name.startswith("rss_") for c in rss))
    names = list_collector_names(include_dummy=False)
    check("list includes rss_", any(n.startswith("rss_") for n in names))
    sample = rss[0]
    got = get_collector(sample.name)
    check("get_collector finds rss", got is not None and got.name == sample.name)


def test_rss_collector_local_feed():
    """不依赖 RSSHub:用本地 Atom/RSS 文件验证解析与入库。"""
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Local Test</title>
        <item>
          <title>Hello Fishnet RSS</title>
          <link>https://example.com/a?utm_source=x</link>
          <description><![CDATA[<p>摘要一段</p>]]></description>
          <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">
            <![CDATA[<p>""" + ("全文内容足够长。" * 20) + """</p>]]>
          </content:encoded>
          <author>tester</author>
          <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
          <category>lab3</category>
        </item>
        <item>
          <title>Long Hegel essay</title>
          <link>https://example.com/hegel</link>
          <description><![CDATA[<p>""" + ("黑格尔不配被称为哲学家，他只不过是个故弄玄虚的臭神棍。" * 40) + """</p>]]></description>
          <pubDate>Wed, 03 Jan 2024 12:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Second</title>
          <link>https://example.com/b</link>
          <description>only summary</description>
          <pubDate>Tue, 02 Jan 2024 12:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "feed.xml"
        path.write_text(xml, encoding="utf-8")
        cfg = {
            "name": "本地测试源",
            "url": path.as_uri(),
            "source": "rss",
            "kind": "article",
        }
        c = RSSCollector(cfg)
        items = list(c.collect())
        check("local feed yields 3", len(items) == 3, str(len(items)))
        check("content extracted", items[0].content is not None and len(items[0].content) > 80)
        check("summary stripped", items[0].summary == "摘要一段")
        check("source enum", items[0].source == Source.RSS)
        check("published tz-aware", items[0].published_at is not None)
        check("tags", "lab3" in items[0].tags)
        check("collector name", c.name.startswith("rss_"))
        long_item = next(it for it in items if "Long Hegel" in it.title)
        check("long rss summary kept full in content", len(long_item.content or "") > 500)
        check("display summary sliced to 500", len(long_item.summary or "") == 500)

        db = Path(td) / "t.db"
        store = Store(db)
        try:
            new, dup = store.upsert_items(items)
            check("upsert new", new == 3 and dup == 0)
            # 再采一次应全 dup
            items2 = list(c.collect())
            new2, dup2 = store.upsert_items(items2)
            check("idempotent", new2 == 0 and dup2 == 3)
        finally:
            store.close()


def test_subscriptions_render():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        store = Store(db)
        try:
            feeds = [
                {
                    "name": "演示源",
                    "url": "http://example/x",
                    "source": "zhihu",
                    "kind": "article",
                    "weight": 1.0,
                }
            ]
            from collectors.rss_generic import slugify

            coll = f"rss_{slugify('演示源')}"
            it = Item(
                source=Source.ZHIHU,
                kind=Kind.ARTICLE,
                title="订阅测试标题",
                url="https://www.zhihu.com/question/1",
                author="demo",
                content="这是印在报纸上的正文,不是一个打开链接。",
                published_at=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
                collector=coll,
            )
            store.upsert_items([it])
            items = collect_subscription_items(
                store, window_hours=24 * 365 * 10, collector_names={coll}
            )
            check("collect_subscription_items", len(items) == 1)
            md = render_subscriptions_md(items, feeds=feeds, window_hours=48)
            check("md has title", "订阅测试标题" in md)
            check("md has body", "印在报纸上的正文" in md)
            check("md has feed list", "演示源" in md)
            check("not a click table", "[打开]" not in md)
            out = Path(td) / "sections"
            path = write_subscriptions_section(
                store, window_hours=24 * 365 * 10, limit=10, out_dir=out
            )
            # write 用真实 feeds;只要文件写出即可
            check("wrote subscriptions.md", path.exists() and path.stat().st_size > 0)
        finally:
            store.close()


def test_lab34_wechat_adr():
    adr = Path(__file__).resolve().parent.parent / "docs/adr/002-wechat-mp-strategy.md"
    example = Path(__file__).resolve().parent.parent / "config/wechat.yaml.example"
    wewe_compose = Path(__file__).resolve().parent.parent / "docker-compose.wewe-rss.yml"
    check("ADR-002 exists", adr.exists())
    check("wechat.yaml.example exists", example.exists())
    check("docker-compose.wewe-rss.yml exists", wewe_compose.exists())
    text = adr.read_text(encoding="utf-8")
    check("ADR mentions WeWe RSS", "WeWe RSS" in text or "wewe" in text.lower())


def test_live_rsshub_smoke():
    if not _rsshub_up():
        print("[SKIP] live RSSHub not reachable — start with: docker compose up -d")
        return
    # 挑几个相对稳的路由做连通性(知乎可能要 Cookie,失败不直接判整 Lab 挂)
    probes = [
        ("bilibili/泛式", "{rsshub}/bilibili/user/video/63231"),
        ("bangumi/today", "{rsshub}/bangumi.tv/calendar/today"),
        ("wsj/official", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
        ("wallstreetcn", "{rsshub}/wallstreetcn/news/global"),
    ]
    base = load_settings().rsshub_url.rstrip("/")
    ok = 0
    details = []
    for label, tmpl in probes:
        url = tmpl.replace("{rsshub}", base)
        try:
            cfg = {
                "name": f"probe_{label}",
                "url": url,
                "source": "bilibili" if "bilibili" in label or "bangumi" in label else "finance",
                "kind": "article",
            }
            items = list(RSSCollector(cfg).collect())
            if items:
                ok += 1
                details.append(f"{label}:{len(items)}")
            else:
                details.append(f"{label}:empty")
        except Exception as e:
            details.append(f"{label}:ERR:{type(e).__name__}")
    check(
        "live RSSHub ≥2 probe feeds ok",
        ok >= 2,
        f"ok={ok} · " + "; ".join(details),
    )


def test_text_cleanup_and_filters():
    from core.text import (
        display_title,
        expand_zhihu_daily_title,
        html_to_text,
        is_zhihu_skip_title,
        readable_body,
        strip_zhihu_footer,
    )

    check("html paragraphs", html_to_text("<p>第一段</p><p>第二段</p>") == "第一段\n\n第二段")
    junk = (
        "正经正文到此结束。 author: cantcatchme@thoughtsmemo 相关文章 "
        "如何显得（并真正做到）深刻 「狭隘」也是一种美德"
    )
    check("zhihu footer stripped", strip_zhihu_footer(junk) == "正经正文到此结束。")
    check("skip 赞同了回答", is_zhihu_skip_title("Thoughts Memo赞同了回答: 如何记忆日语"))
    check("keep 回答了问题", not is_zhihu_skip_title("Thoughts Memo回答了问题: 嘉豪梗"))
    daily_body = (
        "嘿，这里是知乎早报！ 热点速递｜BREAKING NEWS "
        "1、 深圳大学拟拿出5.3亿元买宿舍，背后有哪些考量？ 背景：近日采购意向。"
        "2、 「人民艺术家」郭兰英逝世，她的哪些作品令你印象深刻？ 背景：讣告。"
        "小李精选｜XIAOLI’S PICKS 1、 为什么南极洲看起来那么大？"
    )
    full = expand_zhihu_daily_title("深圳大学拟 5.3 亿元…", daily_body)
    check("daily title expanded", "深圳大学" in full and "郭兰英" in full, full)
    check("daily title skips 小李精选", "南极洲" not in full, full)

    it = Item(
        Source.ZHIHU,
        Kind.ARTICLE,
        "Thoughts Memo赞同了回答: 拟声词",
        "https://www.zhihu.com/question/1/answer/1",
        content=junk,
        collector="rss_thoughts_memo_动态",
    )
    check("readable_body drops footer", "相关文章" not in readable_body(it))

    daily = Item(
        Source.ZHIHU,
        Kind.ARTICLE,
        "深圳大学拟 5.3 亿元买整栋商品房当学生宿舍；《影之刃零》开启预售…",
        "https://zhuanlan.zhihu.com/p/1",
        content=daily_body,
        collector="rss_知乎日报_早报",
    )
    check("display_title prints daily full", "郭兰英" in display_title(daily), display_title(daily))

    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>t</title>
        <item>
          <title>Thoughts Memo赞同了回答: 不该入库</title>
          <link>https://www.zhihu.com/question/1/answer/1</link>
          <description>someone else's answer</description>
        </item>
        <item>
          <title>Thoughts Memo回答了问题: 该留</title>
          <link>https://www.zhihu.com/question/2/answer/2</link>
          <description><![CDATA[<p>第一段</p><p>author: x@y 相关文章 垃圾推荐</p>]]></description>
        </item>
      </channel>
    </rss>
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "zhihu.xml"
        path.write_text(xml, encoding="utf-8")
        cfg = {
            "name": "Thoughts Memo 回答",
            "url": path.as_uri(),
            "source": "zhihu",
            "kind": "article",
        }
        items = list(RSSCollector(cfg).collect())
        check("drops 赞同了", len(items) == 1, str([i.title for i in items]))
        check("keeps 回答了", "该留" in items[0].title)
        check("rss footer stripped", "相关文章" not in (items[0].content or items[0].summary or ""))

    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        try:
            old = Item(
                Source.ZHIHU, Kind.ARTICLE, "十三天前的早报",
                "https://zhuanlan.zhihu.com/p/old",
                content="嘿，这里是知乎早报！" * 5,
                collector="rss_知乎日报_早报",
                published_at=now - timedelta(days=13),
                fetched_at=now,
            )
            video = Item(
                Source.BILIBILI, Kind.VIDEO, "塔菲吐槽",
                "https://www.bilibili.com/video/BV1",
                content="一堆相关推荐标题",
                collector="rss_好柿花生_投稿",
                published_at=now,
                fetched_at=now,
            )
            fresh = Item(
                Source.ZHIHU, Kind.ARTICLE, "今天的回答",
                "https://www.zhihu.com/question/9/answer/9",
                content="今天写的正经回答。" * 8,
                collector="rss_thoughts_memo_回答",
                published_at=now,
                fetched_at=now,
            )
            store.upsert_items([old, video, fresh])
            got = collect_subscription_items(
                store,
                window_hours=48,
                collector_names={
                    "rss_知乎日报_早报",
                    "rss_好柿花生_投稿",
                    "rss_thoughts_memo_回答",
                },
            )
            titles = [i.title for i in got]
            check("drops stale daily", "十三天前的早报" not in titles, str(titles))
            check("drops video", "塔菲吐槽" not in titles, str(titles))
            check("keeps fresh answer", "今天的回答" in titles, str(titles))
            md = render_subscriptions_md(
                got,
                feeds=[{"name": "Thoughts Memo 回答", "url": "x", "source": "zhihu"}],
                already={fresh.content_hash: "已上头版 F02"},
            )
            check("subscribe pointer", "已上头版 F02" in md)
            check("no duplicate body when already ranked", md.count("今天写的正经回答") == 0)
        finally:
            store.close()


def test_title_regex_keeps_zaobao_only():
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>知乎日报</title>
        <item>
          <title>某条新闻；另一条｜早报 20260821</title>
          <link>https://zhuanlan.zhihu.com/p/111</link>
          <description>嘿，这里是知乎早报！编辑部小李准备了每日热点。</description>
        </item>
        <item>
          <title>瞎扯 · 见过哪些离大谱的翻译？</title>
          <link>https://zhuanlan.zhihu.com/p/222</link>
          <description>不该入库</description>
        </item>
        <item>
          <title>朱雀三号成功回收，公积金政策调整取消收...</title>
          <link>https://zhuanlan.zhihu.com/p/333</link>
          <description>嘿，这里是知乎早报！编辑部小李准备了每日热点。</description>
        </item>
      </channel>
    </rss>
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "daily.xml"
        path.write_text(xml, encoding="utf-8")
        cfg = {
            "name": "知乎日报 早报",
            "url": path.as_uri(),
            "source": "zhihu",
            "kind": "article",
            "title_regex": "早报",
        }
        items = list(RSSCollector(cfg).collect())
        check("only zaobao kept", len(items) == 2, str(len(items)))
        titles = {it.title for it in items}
        check("keeps fullwidth pipe title", any("早报 20260821" in t for t in titles))
        check("keeps truncated title via body", any("朱雀三号" in t for t in titles))
        check("drops 瞎扯", not any("瞎扯" in t for t in titles))


def main():
    test_slugify_and_helpers()
    test_feeds_config_coverage()
    test_registry_rss_collectors()
    test_rss_collector_local_feed()
    test_title_regex_keeps_zaobao_only()
    test_subscriptions_render()
    test_text_cleanup_and_filters()
    test_lab34_wechat_adr()
    test_live_rsshub_smoke()
    print("All Lab 3 checks passed (live probes may have been skipped).")


if __name__ == "__main__":
    main()
