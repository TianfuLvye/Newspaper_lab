"""Lab 8 验收:期次目录 → newspaper-layout articles.json。不跑 Chromium 拼版。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render.edition_to_articles import (
    BRIEF_CHARS,
    LONG_CHARS,
    classify_kind,
    edition_to_articles,
    write_articles_json,
)
from render.newspaper import TEMPLATES_DIR
from render.parse_edition import parse_edition_dir

PASS = FAIL = 0
DOC = ROOT / "docs" / "lab-08-render.md"
ADR_OLD = ROOT / "docs" / "adr" / "007-newspaper-grid.md"
ADR_NEW = ROOT / "docs" / "adr" / "009-newspaper-layout-v04.md"
SAMPLE = ROOT / "data" / "editions" / "2026-08-28-am"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


print("\n[Lab 8] 文档与 ADR")
check("lab-08 笔记存在", DOC.exists())
check("ADR-007 仍在(已 superseded)", ADR_OLD.exists())
check("ADR-009 存在", ADR_NEW.exists())
if DOC.exists():
    t = DOC.read_text(encoding="utf-8")
    check("笔记写了 newspaper-layout / articles.json", "newspaper-layout" in t and "articles.json" in t)
if ADR_OLD.exists():
    check("ADR-007 标明 superseded", "superseded" in ADR_OLD.read_text(encoding="utf-8").lower())


print("\n[Lab 8] kind / priority 规则")
check("health → system_report", classify_kind("health", "index", "x" * 800) == "system_report")
check("lede → brief", classify_kind("lede", "index", "综述") == "brief")
check("hotlist index → brief", classify_kind("hotlist", "index", "1. 标题") == "brief")
check("critical 长文 → report", classify_kind("critical", "story", "字" * LONG_CHARS) == "report")
check("短稿 → brief", classify_kind("headline", "story", "短" * 20) == "brief")
check("中稿 → normal", classify_kind("deepread", "story", "中" * 800) == "normal")
check("长稿 → long", classify_kind("oral", "story", "长" * LONG_CHARS) == "long")
check("brief 阈值", BRIEF_CHARS < LONG_CHARS)


print("\n[Lab 8] 模板目录")
check("Guardian 模板目录存在", TEMPLATES_DIR.is_dir(), str(TEMPLATES_DIR))
n_templates = len(list(TEMPLATES_DIR.rglob("template.json")))
check("至少 8 个 template.json", n_templates >= 8, str(n_templates))
try:
    from newspaper_layout.templates import TemplateParser

    loaded = TemplateParser().load(TEMPLATES_DIR)
    check("TemplateParser 能加载", len(loaded) == n_templates, f"{len(loaded)} vs {n_templates}")
    check("有头版模板", any(t.page.type == "front" for t in loaded))
except Exception as e:
    check("TemplateParser 能加载", False, repr(e))


print("\n[Lab 8] 合成期次转换")
with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp)
    (dest / "01_headline.md").write_text(
        "# 头版\n\n"
        "## F01 · 一条很长的头版新闻标题\n\n"
        "> 总分 0.41 · sim 0.65\n"
        "> 今天 10:22\n\n"
        + ("这是头版正文。这是头版正文。" * 200)
        + "\n\n原文地址(需上网): `https://example.com/a`\n"
        "读完再打点: `uv run main.py feedback --edition x --n 1 --label 1`\n",
        encoding="utf-8",
    )
    (dest / "02_hotlist.md").write_text(
        "# 今日新上榜 Top 20\n\n1. **西藏救援** · toutiao · 热度 100\n",
        encoding="utf-8",
    )
    (dest / "07_critical.md").write_text(
        "# 今日一问\n\n## F12 · 一篇足够长的批判稿\n\n"
        + ("批判性思考段落。" * 400)
        + "\n",
        encoding="utf-8",
    )
    (dest / "99_health.md").write_text(
        "# 系统体检\n\n**告警 1 项**\n\n| collector | ok |\n|---|---:|\n| `rss` | 1 |\n",
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
    by_section = {a["metadata"]["section"]: a for a in articles}
    check("解析出头版/热榜/一问/体检/综述", set(by_section) >= {"headline", "hotlist", "critical", "health", "lede"})
    check("id 带 am 前缀", articles[0]["id"].startswith("am"), articles[0]["id"])
    check("头版是 long", by_section["headline"]["kind"] == "long", by_section["headline"]["kind"])
    check("头版 priority 接近 0.97", abs(by_section["headline"]["priority"] - 0.97) < 0.05)
    check("综述是 brief", by_section["lede"]["kind"] == "brief")
    check("体检是 system_report", by_section["health"]["kind"] == "system_report")
    check("体检 priority 最低", by_section["health"]["priority"] < 0.3)
    check("critical 长文是 report", by_section["critical"]["kind"] == "report")
    md = by_section["headline"]["markdown"]
    check("正文去掉打分", "总分" not in md)
    check("正文去掉反馈命令", "feedback" not in md)
    check("正文去掉原文地址", "原文地址" not in md)
    check("空栏目不进 articles", all(a["metadata"]["section"] != "oral" for a in articles))
    path, written, _ = write_articles_json(dest, kind="am")
    check("写出 articles.json", path.exists())
    loaded = json.loads(path.read_text(encoding="utf-8"))
    check("JSON 是数组", isinstance(loaded, list) and loaded)
    check("字段齐全", all(k in loaded[0] for k in ("id", "title", "markdown", "images", "priority", "kind", "metadata")))


print("\n[Lab 8] digest.md 回退")
with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp)
    (dest / "digest.md").write_text(
        "# 自动日报 · 晚报 · 2026-08-30-pm\n\n"
        "> 期号 `2026-08-30-pm` · 生成于 2026-08-30 18:00 CST\n\n"
        "# 头版\n\n## F01 · 晚报头版\n\n晚报正文一段。\n\n---\n\n"
        "# 系统体检\n\n**告警 0 项**\n",
        encoding="utf-8",
    )
    parsed, meta = parse_edition_dir(dest)
    check("digest 回退读到期号", meta.edition_id == "2026-08-30-pm")
    check("digest 回退识别晚报", meta.kind == "pm")
    check("digest 回退拆出栏目", {a.section for a in parsed} >= {"headline", "health"})
    articles, _ = edition_to_articles(dest, kind="pm")
    check("digest 回退 id 带 pm", articles[0]["id"].startswith("pm"))


if SAMPLE.is_dir() and (SAMPLE / "01_headline.md").exists():
    print("\n[Lab 8] 真实期次 2026-08-28-am")
    articles, meta = edition_to_articles(SAMPLE, kind="am")
    check("真实期次有稿", len(articles) >= 10, str(len(articles)))
    check("期号对", meta.edition_id == "2026-08-28-am")
    sections = {a["metadata"]["section"] for a in articles}
    check("含头版和体检", {"headline", "health"} <= sections, str(sections))
    health = next(a for a in articles if a["metadata"]["section"] == "health")
    check("真实体检 kind=system_report", health["kind"] == "system_report")
    ledes = [a for a in articles if a["metadata"]["section"] == "lede"]
    check("有综述稿", bool(ledes))
    scored = [a for a in articles if "总分" in a["markdown"] or "feedback" in a["markdown"]]
    check("真实稿去壳", not scored, f"{len(scored)} 篇仍带打分/反馈")
    imaged = [a for a in articles if a["images"]]
    if imaged:
        img = imaged[0]["images"][0]
        check("图有宽高", img.get("width_px", 0) > 0 and img.get("height_px", 0) > 0, str(img))
        check("图 src 相对或存在", bool(img.get("src")))
    ranked = [a for a in articles if a["metadata"].get("ranking_score", 0) not in (0, 0.2)]
    check("读到 ranking.json 分数", bool(ranked) or not (SAMPLE / "ranking.json").exists())


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
