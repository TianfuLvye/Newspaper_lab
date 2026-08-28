"""Lab 8 验收:A3 矩阵排版、过长分页、1–3 图重排、中文 PDF、空版可见。"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render.layout.engine import density_mode, layout_edition
from render.layout.grid import CellRect, MmRect, PageGeom
from render.layout.images import classify, plan_image_slots, wrap_obstacles
from render.layout.measure import chars_that_fit, column_count, punch_columns, wrap_text, wrap_title, title_wrap_line_count, split_body
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
check("4 栏 × 8 行", g.cols == 4 and g.rows == 8)
full = g.cell_to_mm(CellRect(0, 0, 4, 8))
check("满格覆盖内容区宽度", abs(full.w - g.content_w) < 0.05, f"{full.w} vs {g.content_w}")
check("满格覆盖内容区高度", abs(full.h - g.content_h) < 0.05, f"{full.h} vs {g.content_h}")
a = g.cell_to_mm(CellRect(0, 2, 2, 3))
b = g.cell_to_mm(CellRect(2, 2, 2, 3))
check("相邻块不重叠", a.right <= b.x + 0.01)

print("\n[Lab 8] 天际线 + hole-first")
pack = MaxRects(4, 8)
p1 = pack.place(3, 3)
p2 = pack.place(1, 3)
p3 = pack.place(4, 2)
check("三块都放下", p1 is not None and p2 is not None and p3 is not None)
check("无重叠", no_overlaps(pack.used))
check("超出纸面放不下", pack.place(4, 8) is None)
under = MaxRects(4, 8)
under.occupy(0, 2, 4, 2)
sky = under.find(4, 2)
holes = under.free_rects()
check(
    "天际线贴轮廓,头顶的洞靠 hole-first 看见",
    sky is not None
    and sky.r == 4
    and any(h.r == 0 and h.w == 4 and h.h == 2 for h in holes),
    f"sky={sky} holes={holes}",
)

print("\n[Lab 8] 中文折行")
lines = wrap_text("渔网计划把一张 A3 切成矩阵。Hello, world.", 40, 9)
check("折出行 > 1", len(lines) >= 2, str(lines))
cia_title = "要求基辅暂停攻击，行程目的引发猜测，CIA局长敏感时刻突访莫斯科"
cia_title_lines = wrap_title(cia_title, 41.53, 17.8)
cia_body_lines = wrap_text(cia_title, 41.53, 17.8)
check(
    "窄栏标题按加粗拉丁折行,不少于 6 行",
    len(cia_title_lines) >= 6,
    str(cia_title_lines),
)
check(
    "标题折行比正文 wrap 更保守",
    len(cia_title_lines) > len(cia_body_lines),
    f"title={len(cia_title_lines)} body={len(cia_body_lines)}",
)
commodity = "金银只是开场，所有大宗商品都涨起来了"
check(
    "末行快满的窄栏标题多留一行,避免末字叠到刊出时间",
    title_wrap_line_count(commodity, 41.53, 17.8) >= 4,
    str(wrap_title(commodity, 41.53, 17.8)),
)
n = chars_that_fit("甲" * 400, 60, 20, 9, 1.35, columns=False)
check("高度限制截断", 20 < n < 400, str(n))
check("宽块切成竖栏", column_count(120) >= 2, str(column_count(120)))
from render.layout.measure import column_rects
crs = column_rects(MmRect(0, 0, 180, 80), 4)
check("竖栏比整块窄", len(crs) == 4 and crs[0].w < 50, str(crs[0].w if crs else None))
sample = "甲" * 12 + "。乙" * 40 + "。"
head, tail = split_body(sample, 20)
check("续文在句号切开", (not tail) or head.endswith("。"), repr(head[-8:]))

print("\n[Lab 8] 刊出时间")
from datetime import datetime, timedelta, timezone
from core.text import format_dateline

_cst = timezone(timedelta(hours=8))
_now = datetime(2026, 8, 27, 14, 0, tzinfo=_cst)
check("今天几点几分", format_dateline(_now.replace(hour=9, minute=5), now=_now) == "今天 09:05")
check("昨天几点几分", format_dateline(_now - timedelta(days=1), now=_now) == "昨天 14:00")
check("更早只写年月日", format_dateline(datetime(2026, 8, 20, 8, 0, tzinfo=_cst), now=_now) == "2026年8月20日")
check("没有时间则空", format_dateline(None) == "")
_date_dir = Path(tempfile.mkdtemp())
(_date_dir / "01_headline.md").write_text(
    "# 头版\n\n## F01 · 有日期的稿\n\n> 总分 0.10 · sim 0.10\n> 今天 09:05\n\n正文一段。\n",
    encoding="utf-8",
)
_dated, _ = parse_edition_dir(_date_dir)
_hit = next(a for a in _dated if a.section == "headline" and a.role == "story")
check("题下吃到刊出时间", _hit.byline == "今天 09:05", _hit.byline)
check("刊出时间不进正文", "今天 09:05" not in _hit.body and "总分" not in _hit.body)

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
check("报头占满顶两行", mast[0].cells.w == 4 and mast[0].cells.h == 2)

print("\n[Lab 8] 头版导语 / 内页一版 / 续文打散")
deep_pages = {
    b.page
    for p in lay.pages
    for b in p.blocks
    if b.chunk and b.article_id == "deepread-02"
}
check("深度短稿不上头版", 0 not in deep_pages, str(deep_pages))
check("深度短稿一版装完", len(deep_pages) == 1, str(deep_pages))
front_secs = {
    b.section
    for b in lay.pages[0].blocks
    if b.chunk and b.kind in ("story", "index", "placeholder")
}
check("头版只有 headline", front_secs <= {"headline"}, str(front_secs))

spread_arts = [
    _story(1, 2800, max_pages=4),
    _story(2, 2800, max_pages=4),
    _story(3, 2800, max_pages=4),
]
spread = layout_edition(spread_arts, kind="am", edition_id="spread-am", lede="散")
jump_to = sorted(
    {
        b.jump_to
        for p in spread.pages
        for b in p.blocks
        if b.chunk and b.chunk.part == 0 and b.jump_to
    }
)
check(
    "三条头版续文打散到不同版",
    len(jump_to) >= 3,
    str(jump_to),
)

back_arts = [
    _story(1, 400, max_pages=2),
    _story(2, 500, section="deepread"),
    _story(3, 500, section="deepread"),
    _story(4, 9000, section="subscribe", max_pages=6),
]
back = layout_edition(back_arts, kind="am", edition_id="back-am", lede="后")
first_pg: dict[str, int] = {}
for p in back.pages:
    for b in p.blocks:
        if b.chunk and b.chunk.part == 0:
            first_pg.setdefault(b.article_id, b.page)
short_start = [
    first_pg[k] for k in ("deepread-02", "deepread-03") if k in first_pg
]
check(
    "超长订阅靠后",
    "subscribe-04" in first_pg
    and short_start
    and first_pg["subscribe-04"] > max(short_start),
    str(first_pg),
)


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
    punched3 = punch_columns(p3.text_rect, wrap_obstacles(p3.well, p3.image_boxes))
    check(
        "图井不吞掉全部正文",
        max((c.h for c in punched3), default=0) >= 20,
        f"text_h={max((c.h for c in punched3), default=0)} variant={p3.variant}",
    )

tiny = plan_image_slots(MmRect(0, 0, 18, 18), [img_l, img_p, img_s])
check("格子太小则图溢出到续页", len(tiny.overflow_images) >= 1, tiny.variant)

wide = MmRect(0, 0, 220, 140)
p_wrap = plan_image_slots(wide, [img_l])
n5 = column_count(wide.w)
check("220mm 是 5 栏", n5 == 5, str(n5))
check("宽稿横图走顶井", p_wrap.variant.startswith("top"), p_wrap.variant)
check("绕排正文区仍全宽", p_wrap.text_rect is not None and abs(p_wrap.text_rect.w - wide.w) < 0.1)
punched = punch_columns(wide, wrap_obstacles(p_wrap.well, p_wrap.image_boxes), n5)
side_h = punched[0].h
mid_h = punched[len(punched) // 2].h
check("旁栏高于图下各栏", side_h > mid_h + 8, f"side={side_h:.1f} mid={mid_h:.1f}")
check("旁栏从内容顶起排", abs(punched[0].y - wide.y) < 2, f"y={punched[0].y}")
check(
    "中栏从图下起排",
    p_wrap.well is not None and punched[len(punched) // 2].y >= p_wrap.well.bottom - 0.5,
    f"mid_y={punched[len(punched) // 2].y} well_b={None if p_wrap.well is None else p_wrap.well.bottom}",
)
if p_wrap.well:
    check("图井窄于全文", p_wrap.well.w < wide.w - 10, f"well.w={p_wrap.well.w:.1f}")
    check(
        "图井居中",
        p_wrap.well.x > wide.x + 5 and p_wrap.well.right < wide.right - 5,
        f"x={p_wrap.well.x:.1f} right={p_wrap.well.right:.1f}",
    )
    frac = (p_wrap.well.w * p_wrap.well.h) / (wide.w * wide.h)
    check("井面积 ≤ 45%", frac <= 0.451, f"{frac:.2f}")
p_full = plan_image_slots(wide, [img_l, img_s])
if p_full.well:
    check("两张铺满则井接近全宽", p_full.well.w >= wide.w - 1, f"w={p_full.well.w:.1f}")
    punched2 = punch_columns(wide, wrap_obstacles(p_full.well, p_full.image_boxes), n5)
    check(
        "铺满时各栏都从图下起",
        all(abs(c.y - punched2[0].y) < 1.5 for c in punched2),
        str([round(c.y, 1) for c in punched2]),
    )

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
(tmp / "04_oral.md").write_text("# 口播\n\n_今日口播未成。_\n", encoding="utf-8")
(tmp / "07_critical.md").write_text("# 今日一问\n\n_本栏目今日无入选。_\n", encoding="utf-8")
(tmp / "06_subscribe.md").write_text("# 订阅更新\n\n_本栏目今日无数据。_\n", encoding="utf-8")
(tmp / "99_health.md").write_text("# 系统体检\n\n**告警 0 项**\n", encoding="utf-8")
(tmp / "digest.md").write_text("# 自动日报 · 早报 · test-lab8\n\n> 期号 `test-lab8`\n", encoding="utf-8")

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
check(
    "Inside 是头版左栏目录",
    inside.cells.c == 0 and inside.cells.w == 1 and inside.cells.h >= 6,
    str(inside.cells),
)
check(
    "目录标题印全文且缀粗体字数",
    all(t[1] in html for t in inside.teasers)
    and all(f'<b class="wc">{t[3]} 字</b>' in html for t in inside.teasers if t[3])
    and '<span class="sep">//</span>' in html,
)
_n_arts = len(
    {
        b.article_id
        for p in news.layout.pages
        for b in p.blocks
        if b.chunk and b.kind not in ("masthead", "folio", "inside")
    }
)
check("目录条目覆盖全部稿件", len(inside.teasers) == _n_arts, f"{len(inside.teasers)}/{_n_arts}")
check("稿件之间有细线", "rule-v" in html or "rule-h" in html)
check("正文按段落输出", "<p>" in html)
check("CSS 分栏从左填满", "column-fill: auto" in html)
check("热榜加粗不是星号", "<strong>甲事件</strong>" in html and "**甲事件**" not in html)
from render.html_out import write_html

huge_html = tmp / "huge.html"
write_html(huge, huge_html)
ht = huge_html.read_text(encoding="utf-8")
check(
    "续文用原题+上接指路,不印编号",
    ht.count("测试稿件01") >= 2
    and "上接第" in ht
    and not re.search(r"（上接", ht)
    and not re.search(r"上接[^<]*·\s*F", ht),
)
cut = layout_edition(
    [
        Article(
            id="headline-cut",
            section="headline",
            role="story",
            title="删节测试：安全阀超限时必须认账",
            kicker="头版",
            body="裁我请在段落边界。这是一段会被安全阀切掉的长正文。" * 40,
            images=[],
            priority=0,
            max_chars=400,
            max_pages=9,
        )
    ],
    kind="am",
    edition_id="cut-am",
    lede="裁",
)
cut_html = tmp / "cut.html"
write_html(cut, cut_html)
ct = cut_html.read_text(encoding="utf-8")
check(
    "超预算印「有删节」,绝不印指路文件路径",
    "（本文有删节）" in ct and "items/" not in ct and "未完" not in ct,
)
whole_html = tmp / "whole.html"
write_html(huge, whole_html)
wt = whole_html.read_text(encoding="utf-8")
check(
    "预算内长稿全文见报,不印删节",
    "（本文有删节）" not in wt and "items/" not in wt,
)
from html import unescape as _unesc
from render.layout.measure import line_height_mm as _lh_mm

bad_titles = []
for m in re.finditer(
    r'<div class="title" style="[^"]*?width:([\d.]+)mm;height:([\d.]+)mm;'
    r'font-size:([\d.]+)pt">(.*?)</div>',
    ht + html,
    re.S,
):
    w_, h_, s_ = float(m.group(1)), float(m.group(2)), float(m.group(3))
    t_ = _unesc(re.sub(r"<[^>]+>", "", m.group(4)))
    need = len(wrap_title(t_, w_, s_)) * _lh_mm(s_, 1.12)
    if need > h_ + 1.0:
        bad_titles.append((t_[:10], round(need, 1), h_))
check("标题按自然高度占位,盒高装得下整题", not bad_titles, str(bad_titles[:3]))
skinny = [
    (b.article_id, b.cells.w, b.cells.h)
    for p in news.layout.pages
    for b in p.blocks
    if b.kind == "story" and b.cells.w < 2
]
check("稿件至少两栏宽", not skinny, str(skinny))
wide = [
    b.n_text_cols
    for p in news.layout.pages
    for b in p.blocks
    if b.kind == "story" and b.cells.w >= 4
]
check("宽稿切成多条竖栏", not wide or max(wide) >= 2, str(wide))

print("\n[Lab 8] 真实配图文件")
from PIL import Image as PILImage

img_dir = tmp / "images"
img_dir.mkdir(exist_ok=True)
jpg = img_dir / "hero.jpg"
PILImage.new("RGB", (1600, 900), (30, 40, 50)).save(jpg, format="JPEG")
(tmp / "01_headline.md").write_text(
    "# 头版\n\n## F01 · 带图稿\n\n"
    "![横图](images/hero.jpg)\n\n"
    "这是早餐该读完的一段中文。配图应该进图井。\n\n"
    + ("第二段把格子撑高。" * 20)
    + "\n",
    encoding="utf-8",
)
news_img = render_newspaper(tmp, kind="am", edition_id="test-lab8-img")
parsed_img, _ = parse_edition_dir(tmp)
art_img = next(a for a in parsed_img if a.section == "headline" and a.role == "story")
check("解析到本地图", bool(art_img.images) and "hero.jpg" in art_img.images[0].src)
check(
    "读到像素宽高",
    art_img.images[0].width_px == 1600 and art_img.images[0].height_px == 900,
    f"{art_img.images[0].width_px}x{art_img.images[0].height_px}",
)
n_real = sum(len(b.image_boxes) for p in news_img.layout.pages for b in p.blocks)
check("排进图框", n_real >= 1, str(n_real))
html_img = news_img.html_path.read_text(encoding="utf-8")
check("HTML 用相对路径", "images/hero.jpg" in html_img)
check("PDF 仍写出", bool(news_img.pdf_path and news_img.pdf_path.exists()))

from main import build_parser

p = build_parser()
check("pdf 子命令登记", "pdf" in p.format_help())

print(f"\n{'='*60}\n  PASSED {PASS}   FAILED {FAIL}\n{'='*60}")
sys.exit(1 if FAIL else 0)
