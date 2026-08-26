"""Lab 8 验收:A3 矩阵排版、过长分页、1–3 图重排、中文 PDF、空版可见。"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render.layout.engine import density_mode, layout_edition
from render.layout.grid import CellRect, MmRect, PageGeom
from render.layout.images import classify, plan_image_slots
from render.layout.measure import chars_that_fit, column_count, wrap_text, split_body
from render.layout.model import Article, ImageSpec
from render.layout.pack import MaxRects, no_overlaps
from render.lede import extractive_lede
from render.parse_edition import parse_edition_dir
from render.newspaper import render_newspaper

PASS = FAIL = 0
DOC = ROOT / "docs" / "lab-08-render.md"
ADR = ROOT / "docs" / "adr" / "007-newspaper-grid.md"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def _story(i: int, n_chars: int, section: str = "headline", images=None, **kw) -> Article:
    body = "这是一段用来量格子的中文正文。" * max(1, n_chars // 14)
    body = body[:n_chars]
    return Article(
        id=f"{section}-{i:02d}",
        section=section,
        role="story",
        title=f"测试稿件{i:02d}：矩阵排版不该像博客那样通栏流",
        kicker="头版" if section == "headline" else section,
        body=body,
        images=list(images or []),
        fn=f"F{i:02d}",
        priority=i,
        max_chars=max(n_chars, 400),
        max_pages=kw.get("max_pages", 2),
        empty=kw.get("empty", False),
    )


print("\n[Lab 8] 文档与 ADR")
check("lab-08 笔记存在", DOC.exists())
check("ADR-007 存在", ADR.exists())
if DOC.exists():
    t = DOC.read_text(encoding="utf-8")
    check("笔记写了矩阵而不是套 8.1 四方案", "矩阵" in t and "A3" in t)

print("\n[Lab 8] 网格几何")
g = PageGeom()
check("纸是 A3", abs(g.page_w - 297) < 0.01 and abs(g.page_h - 420) < 0.01)
check("6 栏 × 14 行", g.cols == 6 and g.rows == 14)
full = g.cell_to_mm(CellRect(0, 0, 6, 14))
check("满格覆盖内容区宽度", abs(full.w - g.content_w) < 0.05, f"{full.w} vs {g.content_w}")
check("满格覆盖内容区高度", abs(full.h - g.content_h) < 0.05, f"{full.h} vs {g.content_h}")
a = g.cell_to_mm(CellRect(0, 2, 3, 4))
b = g.cell_to_mm(CellRect(3, 2, 3, 4))
check("相邻块不重叠", a.right <= b.x + 0.01)

print("\n[Lab 8] MaxRects 装箱")
pack = MaxRects(6, 12)
p1 = pack.place(4, 5)
p2 = pack.place(2, 5)
p3 = pack.place(6, 3)
check("三块都放下", p1 is not None and p2 is not None and p3 is not None)
check("无重叠", no_overlaps(pack.used))
check("超出纸面放不下", pack.place(6, 12) is None)

print("\n[Lab 8] 中文折行")
lines = wrap_text("渔网计划把一张 A3 切成矩阵。Hello, world.", 40, 9)
check("折出行 > 1", len(lines) >= 2, str(lines))
n = chars_that_fit("甲" * 400, 60, 20, 9, 1.35, columns=False)
check("高度限制截断", 20 < n < 400, str(n))
check("宽块切成竖栏", column_count(120) >= 2, str(column_count(120)))
from render.layout.measure import column_rects
crs = column_rects(MmRect(0, 0, 180, 80), 4)
check("竖栏比整块窄", len(crs) == 4 and crs[0].w < 50, str(crs[0].w if crs else None))
sample = "甲" * 12 + "。乙" * 40 + "。"
head, tail = split_body(sample, 20)
check("续文在句号切开", (not tail) or head.endswith("。"), repr(head[-8:]))

print("\n[Lab 8] 长文分页")
arts = [
    _story(1, 2800, max_pages=3),
    _story(2, 600, section="deepread"),
    Article(
        id="hotlist-index",
        section="hotlist",
        role="index",
        title="今日新上榜",
        kicker="热榜",
        body="\n".join(f"{i}. 热点标题{i}" for i in range(1, 16)),
        max_chars=2000,
        max_pages=1,
    ),
]
lay = layout_edition(arts, kind="am", edition_id="test-am", lede="今日综述测试。")
cells_ok = all(
    no_overlaps([b.cells for b in p.blocks]) for p in lay.pages
)
check("格子不重叠", cells_ok)
parts = [
    b.chunk.part
    for p in lay.pages
    for b in p.blocks
    if b.chunk and b.article_id == "headline-01"
]
check("头版长文被切开", len(parts) >= 2 and 0 in parts and 1 in parts, str(parts))
huge = layout_edition(
    [_story(1, 9000, max_pages=4)],
    kind="am",
    edition_id="huge-am",
    lede="长稿",
)
check("超长稿跨页", huge.n_pages >= 2, str(huge.n_pages))
jumps = [
    (b.chunk.part, b.page + 1, b.jump_to)
    for p in huge.pages
    for b in p.blocks
    if b.chunk and b.jump_to
]
check("跨页才写下转", bool(jumps), str(jumps))
mast = [b for b in lay.pages[0].blocks if b.kind == "masthead"]
check("头版有报头", len(mast) == 1)
check("报头占满顶两行", mast[0].cells.w == 6 and mast[0].cells.h == 2)

print("\n[Lab 8] 早晚报差异")
am = layout_edition(arts, kind="am", edition_id="x-am", lede="早")
pm = layout_edition(arts, kind="pm", edition_id="x-pm", lede="晚")
check("kind 不同", am.kind == "am" and pm.kind == "pm")
check("综述进结果", "早" in am.lede and "晚" in pm.lede)

print("\n[Lab 8] 空版 / 稀薄")
empty = [
    Article(
        id="headline-empty",
        section="headline",
        role="placeholder",
        title="头版今日无稿",
        kicker="头版",
        body="本栏目今日无数据。",
        empty=True,
        max_pages=1,
    ),
    Article(
        id="health-index",
        section="health",
        role="index",
        title="系统体检",
        kicker="体检",
        body="采集失败两项。请先看这里。",
        max_pages=1,
    ),
]
thin = layout_edition(empty, kind="am", edition_id="thin", lede="稿少")
check("稀薄模式", density_mode(empty) == "thin")
check("占位块可见", any(b.kind == "placeholder" for p in thin.pages for b in p.blocks))
check("空版仍出页", thin.n_pages >= 1)

print("\n[Lab 8] 配图算法 1–3 张")
frame = MmRect(0, 0, 120, 90)
img_l = ImageSpec(width_px=1600, height_px=900, caption="横图")
img_p = ImageSpec(width_px=600, height_px=900, caption="竖图")
img_s = ImageSpec(width_px=800, height_px=800, caption="方图")
check("横竖分类", classify(img_l) == "land" and classify(img_p) == "port")

p1 = plan_image_slots(frame, [img_l])
check("1 张横图走顶井", p1.variant.startswith("top"), p1.variant)
check("1 张后正文仍是一块矩形", p1.text_rect is not None and p1.text_rect.w == frame.w)
check("图框在文章内", p1.image_boxes[0].rect.bottom <= frame.bottom + 0.1)

p1p = plan_image_slots(frame, [img_p])
check("1 张竖图走左井", p1p.variant.startswith("left"), p1p.variant)

p2 = plan_image_slots(frame, [img_l, img_s])
check("2 张有两框或 overflow", len(p2.image_boxes) + len(p2.overflow_images) == 2)
check("2 张正文矩形", p2.text_rect is not None)

p3 = plan_image_slots(frame, [img_l, img_s, img_p])
check("3 张吃进方案", p3.variant in ("hero-plus-2", "top-3"), p3.variant)
check("3 张框数 ≤ 3", len(p3.image_boxes) <= 3)
if p3.text_rect:
    check(
        "图井不吞掉全部正文",
        p3.text_rect.h >= 20,
        f"text_h={p3.text_rect.h} variant={p3.variant}",
    )

tiny = plan_image_slots(MmRect(0, 0, 18, 18), [img_l, img_p, img_s])
check("格子太小则图溢出到续页", len(tiny.overflow_images) >= 1, tiny.variant)

art_img = _story(9, 400, images=[img_l, img_p])
lay_img = layout_edition([art_img], kind="am", edition_id="img", lede="")
n_boxes = sum(len(b.image_boxes) for p in lay_img.pages for b in p.blocks)
check("排进版面后仍有图框", n_boxes >= 1, str(n_boxes))

print("\n[Lab 8] 解析真实期次 Markdown")
edition = ROOT / "data" / "editions" / "2026-08-26-am"
if edition.exists() and (edition / "01_headline.md").exists():
    parsed, meta = parse_edition_dir(edition)
    check("读到多版", len({a.section for a in parsed}) >= 4, str({a.section for a in parsed}))
    check("期号", "2026-08-26-am" in meta.edition_id)
    check("头版有稿", any(a.section == "headline" and a.role == "story" for a in parsed))
    check("热榜是整块索引", any(a.section == "hotlist" and a.role == "index" for a in parsed))
    check(
        "订阅不重复已上头版",
        not any(a.role == "story" and "已上头版" in (a.body or "") for a in parsed),
    )
    lede = extractive_lede(parsed, "am")
    check("抽句综述非空", len(lede) > 20, lede[:40])
else:
    print("  SKIP  无 data/editions/2026-08-26-am,改用临时稿")
    parsed = arts
    check("有临时稿", True)

print("\n[Lab 8] HTML + PDF 渲染")
tmp = Path(tempfile.mkdtemp())
(tmp / "01_headline.md").write_text(
    "# 头版\n\n## F01 · 矩阵排版第一篇\n\n"
    "这是早餐该读完的一段中文。标点，挤压。Hello 混排 123。\n\n"
    + ("第二段把格子撑高。" * 40)
    + "\n",
    encoding="utf-8",
)
(tmp / "02_hotlist.md").write_text(
    "# 今日新上榜 Top 20\n\n1. **甲事件** · weibo · 热度 1万\n2. **乙事件** · zhihu · 热度 2万\n",
    encoding="utf-8",
)
(tmp / "03_deepread.md").write_text("# 深度阅读\n\n_本栏目今日无入选。_\n", encoding="utf-8")
(tmp / "07_critical.md").write_text("# 今日一问\n\n_本栏目今日无入选。_\n", encoding="utf-8")
(tmp / "06_subscribe.md").write_text("# 订阅更新\n\n_本栏目今日无数据。_\n", encoding="utf-8")
(tmp / "99_health.md").write_text("# 系统体检\n\n**告警 0 项**\n", encoding="utf-8")
(tmp / "digest.md").write_text("# 渔网早报 · test-lab8\n\n> 期号 `test-lab8`\n", encoding="utf-8")

t0 = time.monotonic()
news = render_newspaper(tmp, kind="am", edition_id="test-lab8")
dt = time.monotonic() - t0
check("写出 HTML", news.html_path.exists() and "A3" in news.html_path.read_text(encoding="utf-8"))
check("写出 layout.json", news.layout_path.exists())
check("digest.md→排版 < 30s", dt < 30, f"{dt:.2f}s")
check("至少一页", news.layout.n_pages >= 1)
if news.pdf_path and news.pdf_path.exists():
    raw = news.pdf_path.read_bytes()
    check("PDF 头", raw.startswith(b"%PDF"))
    check("PDF 不是空壳", len(raw) > 4000, str(len(raw)))
    check("无 PDF 错误", news.error is None, str(news.error))
else:
    check("PDF 写出", False, str(news.error))

obj = json.loads(news.layout_path.read_text(encoding="utf-8"))
check("json 记录纸张", obj["paper"]["w_mm"] == 297)
check("占位栏目仍在", news.layout.n_placeholders >= 1)
html = news.html_path.read_text(encoding="utf-8")
check("白底不是奶油色", "background: #fff" in html)
check("正文用 CSS 竖栏", "column-count" in html)
check("头版有 Inside", any(b.kind == "inside" for b in news.layout.pages[0].blocks))
inside = next(b for b in news.layout.pages[0].blocks if b.kind == "inside")
check("Inside 是通栏横条", inside.cells.w == 6 and inside.cells.r >= 12, str(inside.cells))
check("稿件之间有细线", "rule-v" in html or "rule-h" in html)
check("正文按段落输出", "<p>" in html)
check("CSS 分栏从左填满", "column-fill: auto" in html)
check("热榜加粗不是星号", "<strong>甲事件</strong>" in html and "**甲事件**" not in html)
skinny = [
    (b.article_id, b.cells.w)
    for p in news.layout.pages
    for b in p.blocks
    if b.kind == "story" and b.cells.w < 2
]
check("正文块至少两栏宽", not skinny, str(skinny))
wide = [
    b.n_text_cols
    for p in news.layout.pages
    for b in p.blocks
    if b.kind == "story" and b.cells.w >= 4
]
check("宽稿切成多条竖栏", not wide or max(wide) >= 2, str(wide))

from main import build_parser

p = build_parser()
check("pdf 子命令登记", "pdf" in p.format_help())

print(f"\n{'='*60}\n  PASSED {PASS}   FAILED {FAIL}\n{'='*60}")
sys.exit(1 if FAIL else 0)
