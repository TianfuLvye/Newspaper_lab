"""移动端 edition.json:打印契约之外的客户端字段,不跑 Chromium。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render.edition_to_articles import edition_to_articles
from render.edition_to_client import (
    BRAND,
    edition_to_client,
    export_all_client_editions,
    write_client_edition,
)

PASS = FAIL = 0
SAMPLE = ROOT / "data" / "editions" / "2026-08-30-pm"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


print("\n[Client] 合成期次")
with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp) / "test-am"
    dest.mkdir()
    (dest / "01_headline.md").write_text(
        "# 头版\n\n"
        "## F01 · 一条很长的头版新闻标题\n\n"
        "> 总分 0.41 · sim 0.65\n"
        "> 今天 10:22\n\n"
        + ("这是头版正文。这是头版正文。" * 80)
        + "\n\n原文地址(需上网): `https://example.com/a`\n"
        "读完再打点: `uv run main.py feedback --edition x --n 1 --label 1`\n",
        encoding="utf-8",
    )
    (dest / "items").mkdir()
    (dest / "items" / "01-headline.md").write_text(
        "# 一条很长的头版新闻标题\n\n"
        "> 今天 10:22 · finance · 半导体行业观察\n\n"
        + ("这是头版正文。这是头版正文。" * 80)
        + "\n\n风险提示及免责条款\n\n市场有风险。\n"
        + "\n\n原文地址(需上网,纸上看不到点): `https://example.com/a`\n",
        encoding="utf-8",
    )
    (dest / "02_hotlist.md").write_text(
        "# 今日新上榜 Top 20\n\n"
        "热榜本身是标题流,没有文章。\n\n"
        "1. **西藏救援** · toutiao · 今天 18:49 · 热度 100\n\n"
        "2. **尼泊尔冰崩** · zhihu · 今天 08:41 · 热度 90\n"
        "   这是一条摘要。\n\n"
        "3. **军训结束** · douyin · 今天 14:21 · 热度 80\n",
        encoding="utf-8",
    )
    (dest / "00_lede.md").write_text(
        "# 今日综述\n\n> 来源 `extractive`\n\n今晨：头版有一条长新闻。\n",
        encoding="utf-8",
    )
    (dest / "digest.md").write_text(
        "# 自动日报 · 早报 · test-am\n\n> 期号 `test-am` · 生成于 2026-08-30 12:00 CST\n",
        encoding="utf-8",
    )
    articles, meta = edition_to_articles(dest, kind="am")
    payload = edition_to_client(dest, kind="am", articles=articles, meta=meta)
    headline = next(a for a in payload["articles"] if a["section"] == "headline")
    hot = [a for a in payload["articles"] if a["section"] == "hotlist"]
    check("brand 是自动日报", payload["brand"] == BRAND)
    check("早报 period_label", payload["period_label"] == "早报")
    check("文章没有嵌套 metadata", all("metadata" not in a for a in payload["articles"]))
    check("头版有原文 URL", headline["original_url"] == "https://example.com/a")
    check("头版来源名", headline["source_name"] == "半导体行业观察")
    check("头版来源类型", headline["source_type"] == "finance")
    check("头版刊出时间", headline["published_label"] == "今天 10:22")
    check("头版有摘要", len(headline["summary"]) > 10)
    check("阅读时长至少 1 分钟", headline["reading_minutes"] >= 1)
    check("热榜拆成多条", len(hot) == 3, str(len(hot)))
    check("热榜来源中文", hot[0]["source_name"] == "头条", hot[0]["source_name"])
    check("热榜第二条带摘要", "摘要" in hot[1]["markdown"])
    check("lede 仍在数据层", any(a["section"] == "lede" for a in payload["articles"]))
    check("去掉风险提示", "风险提示" not in headline["markdown"])
    path, written = write_client_edition(dest, kind="am", articles=articles, meta=meta)
    check("写出 edition.json", path.exists())
    loaded = json.loads(path.read_text(encoding="utf-8"))
    check("JSON brand 对齐", loaded["brand"] == BRAND)
    check("index.json latest", json.loads((dest.parent / "index.json").read_text())["latest"] == dest.name)


print("\n[Client] 多期索引")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name, kind in (("2026-08-28-am", "am"), ("2026-08-30-pm", "pm")):
        d = root / name
        d.mkdir()
        (d / "digest.md").write_text(
            f"# 自动日报 · {'早报' if kind == 'am' else '晚报'} · {name}\n\n"
            f"> 期号 `{name}`\n\n# 头版\n\n## F01 · {name} 头版\n\n正文。\n",
            encoding="utf-8",
        )
    index = export_all_client_editions(root)
    check("latest 是最新一期", index["latest"] == "2026-08-30-pm", index["latest"])
    check("两期都进索引", len(index["editions"]) == 2, str(index["editions"]))
    check("晚报标签", index["editions"][0]["period_label"] == "晚报")


if SAMPLE.is_dir() and (SAMPLE / "01_headline.md").exists():
    print("\n[Client] 真实期次 2026-08-30-pm")
    payload = edition_to_client(SAMPLE, kind="pm")
    sections = {a["section"] for a in payload["articles"]}
    check("真实期次有稿", len(payload["articles"]) >= 10, str(len(payload["articles"])))
    check("含头版和热榜", {"headline", "hotlist"} <= sections, str(sections))
    urls = [a["original_url"] for a in payload["articles"] if a["section"] == "headline"]
    check("头版带原文链接", any(u.startswith("http") for u in urls), str(urls[:3]))
    hot = [a for a in payload["articles"] if a["section"] == "hotlist"]
    check("真实热榜拆开", len(hot) >= 5, str(len(hot)))
    named = [a for a in payload["articles"] if a["section"] == "headline" and a["source_name"]]
    check("头版有来源名", bool(named), str(named[:1]))
    print_articles, _ = edition_to_articles(SAMPLE, kind="pm")
    print_hot = [a for a in print_articles if a["metadata"]["section"] == "hotlist"]
    check("打印端热榜仍是一页", len(print_hot) == 1, str(len(print_hot)))
    path, _ = write_client_edition(SAMPLE, kind="pm")
    check("真实期次写出 edition.json", path.exists())


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
