"""Lab 5 验收:本地 20 页语料、质量降级、HTML 缓存、入库回填。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from core.schema import Item, Kind, Source
from core.store import Store
from enrich.extract import (
    PoliteFetcher,
    extract,
    fill_item_content,
    quality_score,
    wallstreetcn_api_url,
)
from tests.fixtures.extract_pages import PAGES

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "lab-05-extract.md"


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def test_quality_score_nav_vs_article():
    article = "这是一段足够长的新闻正文。" * 40
    nav = "首页 登录 注册 下载APP 相关推荐 热门评论 " * 8
    check("article score higher", quality_score(article) > quality_score(nav))
    check("empty is 0", quality_score("") == 0.0)


def test_corpus_success_rate():
    ok = 0
    expected_ok = 0
    for url, html, want_ok in PAGES:
        r = extract(url, html)
        if want_ok:
            expected_ok += 1
            if r.ok and r.text:
                ok += 1
            else:
                print(f"  miss {url} tier={r.tier} score={r.score:.2f} err={r.error}")
        else:
            check(
                f"degrade {url}",
                (not r.ok) or r.text is None,
                f"tier={r.tier}",
            )
    rate = ok / expected_ok
    check(
        "corpus ≥80% of expected-ok",
        rate >= 0.8,
        f"{ok}/{expected_ok} = {rate:.0%}",
    )
    check("corpus has 20 urls", len(PAGES) == 20, str(len(PAGES)))


def test_cache_skips_second_fetch():
    html = (
        "<html><body><article><h1>缓存页</h1><p>"
        + ("正文段落。" * 80)
        + "</p></article></body></html>"
    )
    hits = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        hits["page"] += 1
        return httpx.Response(200, text=html)

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(tmp, delay_seconds=0, client=client)
    url = "https://cache.example.com/story"
    try:
        r1 = extract(url, fetcher=fetcher)
        r2 = extract(url, fetcher=fetcher)
    finally:
        fetcher.close()
        client.close()
    check("first extract ok", r1.ok and bool(r1.text))
    check("second from cache", r2.from_cache is True)
    check("network once", hits["page"] == 1, str(hits["page"]))


def test_robots_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="<html><body>should not see</body></html>")

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(tmp, delay_seconds=0, client=client)
    try:
        r = extract("https://blocked.example.com/secret", fetcher=fetcher)
    finally:
        fetcher.close()
        client.close()
    check("robots -> blocked", r.tier == "blocked", r.tier)
    check("no text when blocked", r.text is None)


def test_enrich_store_fills_content():
    tmp = Path(tempfile.mkdtemp()) / "lab5.db"
    store = Store(tmp)
    url, html, _want = PAGES[0]
    item = Item(
        source=Source.NEWS,
        kind=Kind.ARTICLE,
        title="港口恢复通航",
        url=url,
        summary="短摘要",
        collector="test_lab5",
    )
    store.upsert_items([item])
    missing = store.items_missing_content(limit=10)
    check("missing before enrich", len(missing) == 1)

    r = extract(url, html)
    check("extract ok for store item", r.ok and bool(r.text))
    store.update_content(item.content_hash, r.text or "")
    got = store.get_item(item.content_hash)
    check("content persisted", got is not None and bool(got.content))
    check("still missing none", store.items_missing_content(limit=10) == [])
    store.close()


def test_weixin_robots_override_only_article_path():
    """公众号 robots 是 Disallow:/,个人订阅只放行 /s 单篇。"""
    url, html, _want = next(p for p in PAGES if "mp.weixin.qq.com/s/" in p[0] and p[2])
    hits = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        hits["page"] += 1
        return httpx.Response(200, text=html)

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    blocked = PoliteFetcher(tmp / "no", delay_seconds=0, client=client)
    allowed = PoliteFetcher(
        tmp / "yes",
        delay_seconds=0,
        client=client,
        robots_override_hosts=("mp.weixin.qq.com",),
    )
    try:
        r_block = extract(url, fetcher=blocked)
        r_ok = extract(url, fetcher=allowed)
        r_list = extract(
            "https://mp.weixin.qq.com/mp/homepage", fetcher=allowed
        )
    finally:
        blocked.close()
        allowed.close()
        client.close()
    check("weixin without override blocked", r_block.tier == "blocked", r_block.tier)
    check("weixin /s override ok", r_ok.ok and bool(r_ok.text), r_ok.tier)
    check("weixin fetched article html", hits["page"] == 1, str(hits["page"]))
    check("weixin list still blocked", r_list.tier == "blocked", r_list.tier)


def test_zhihu_override_column_and_answer_not_hot_question():
    url, html, _want = next(
        p for p in PAGES if "zhuanlan.zhihu.com/p/" in p[0] and p[2]
    )
    hits = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        hits["page"] += 1
        return httpx.Response(200, text=html)

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosts = ("zhuanlan.zhihu.com", "www.zhihu.com")
    allowed = PoliteFetcher(
        tmp / "yes",
        delay_seconds=0,
        client=client,
        robots_override_hosts=hosts,
    )
    try:
        r_col = extract(url, fetcher=allowed)
        r_ans = extract(
            "https://www.zhihu.com/question/123/answer/456", fetcher=allowed
        )
        r_hot = extract(
            "https://www.zhihu.com/question/123", fetcher=allowed
        )
    finally:
        allowed.close()
        client.close()
    check("zhihu column override ok", r_col.ok and bool(r_col.text), r_col.tier)
    check("zhihu answer path fetched", r_ans.tier != "blocked", r_ans.tier)
    check("zhihu hot question still blocked", r_hot.tier == "blocked", r_hot.tier)


def test_wallstreetcn_uses_json_api_not_spa():
    spa = (
        "<html><body><div id='app'></div>"
        "<script src='/bundle.js'></script></body></html>"
    )
    body = "华尔街见闻正文段落。" * 40
    payload = {"code": 20000, "data": {"title": "美股收盘", "content": f"<p>{body}</p>"}}
    hits = {"html": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if "/apiv1/content/articles/" in path:
            hits["api"] += 1
            return httpx.Response(200, json=payload)
        if "/articles/" in path:
            hits["html"] += 1
            return httpx.Response(200, text=spa)
        return httpx.Response(404)

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(tmp, delay_seconds=0, client=client)
    url = "https://wallstreetcn.com/articles/3765432"
    member = "https://wallstreetcn.com/member/articles/3765432"
    premium = "https://wallstreetcn.com/premium/articles/3765432?layout=wscn-layout"
    try:
        r = extract(url, fetcher=fetcher)
        r_m = extract(member, fetcher=fetcher)
    finally:
        fetcher.close()
        client.close()
    check(
        "wscn api url",
        wallstreetcn_api_url(url)
        == "https://api.wallstreetcn.com/apiv1/content/articles/3765432?extract=1",
    )
    check("wscn from json api", r.ok and r.extractor == "wallstreetcn_api", r.extractor)
    check("wscn skipped spa html", hits["html"] == 0 and hits["api"] == 2, str(hits))
    check("wscn body kept", (r.text or "").startswith("华尔街见闻正文"))
    check(
        "wscn member api url",
        wallstreetcn_api_url(member)
        == "https://api.wallstreetcn.com/apiv1/content/articles/3765432?extract=1",
    )
    check(
        "wscn premium api url",
        wallstreetcn_api_url(premium)
        == "https://api.wallstreetcn.com/apiv1/content/articles/3765432?extract=1",
    )
    check("wscn member from api", r_m.ok and r_m.extractor == "wallstreetcn_api", r_m.extractor)


def test_wallstreetcn_live_short_is_partial():
    """快讯全文经常 <200 字,这就是完整条目。"""
    body = (
        "据贵州茅台，i茅台APP内贵州茅台酒产品的每日投放时段作出优化调整："
        "自8月24日09:00起，在原有20:00、20:09晚间投放时段基础上，"
        "开设09:00、09:09上午投放时段，产品购买规则不变。"
    )
    payload = {"code": 20000, "data": {"title": "i茅台", "content": f"<p>{body}</p>"}}
    hits = {"html": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if "/apiv1/content/lives/" in path:
            hits["api"] += 1
            return httpx.Response(200, json=payload)
        hits["html"] += 1
        return httpx.Response(200, text="<html><body><div id='app'></div></body></html>")

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(tmp, delay_seconds=0, client=client)
    try:
        r = extract(
            "https://wallstreetcn.com/livenews/3153434",
            fetcher=fetcher,
            fallback_text=body,
        )
        r_chart = extract(
            "https://wallstreetcn.com/charts/41959659",
            fetcher=fetcher,
            fallback_text="图表说明只有一句。",
        )
    finally:
        fetcher.close()
        client.close()
    check("live short ok", r.ok and r.tier == "partial", f"{r.tier} n={len(r.text or '')}")
    check("live used api", r.extractor == "wallstreetcn_api" and hits["api"] == 1, r.extractor)
    check("chart skipped", r_chart.tier == "meta_only" and r_chart.extractor == "skip_chart", r_chart.extractor)
    check("chart did not fetch html", hits["html"] == 0, str(hits))


def test_rss_summary_fallback_when_html_empty():
    spa = "<html><body><div id='app'></div></body></html>"
    summary = "快讯：美联储官员称通胀仍具粘性，市场正在重新定价降息路径。" * 8
    r = extract(
        "https://wallstreetcn.com/livenews/12345",
        html=spa,
        fallback_text=summary,
    )
    check("fallback ok", r.ok and bool(r.text), f"tier={r.tier} n={len(r.text or '')}")
    check("fallback tagged", "rss_fallback" in r.extractor, r.extractor)

    item = Item(
        source=Source.NEWS,
        kind=Kind.ARTICLE,
        title="被墙的公众号",
        url="https://blocked.example.com/secret",
        summary=summary,
        collector="test_lab5",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="<html></html>")

    tmp = Path(tempfile.mkdtemp()) / "cache"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(tmp, delay_seconds=0, client=client)
    try:
        r2 = fill_item_content(item, fetcher=fetcher)
    finally:
        fetcher.close()
        client.close()
    check("blocked uses summary", r2.ok and item.content == r2.text, r2.tier)
    check("blocked fallback tagged", "rss_fallback" in r2.extractor, r2.extractor)


def test_docs():
    check("lab-05 doc exists", DOC.exists())
    text = DOC.read_text(encoding="utf-8")
    for key in ("trafilatura", "缓存", "robots", "降级", "华尔街见闻", "个人订阅"):
        check(f"doc mentions {key}", key in text)


def main():
    test_quality_score_nav_vs_article()
    test_corpus_success_rate()
    test_cache_skips_second_fetch()
    test_robots_blocks()
    test_weixin_robots_override_only_article_path()
    test_zhihu_override_column_and_answer_not_hot_question()
    test_wallstreetcn_uses_json_api_not_spa()
    test_wallstreetcn_live_short_is_partial()
    test_rss_summary_fallback_when_html_empty()
    test_enrich_store_fills_content()
    test_docs()
    print("All Lab 5 checks passed.")


if __name__ == "__main__":
    main()
