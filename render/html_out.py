"""同一套矩阵坐标的 HTML。白底细线、竖栏正文,和 PDF 同一套美术。"""
from __future__ import annotations

import html
from pathlib import Path

from render.layout.grid import MmRect
from render.layout.measure import TypeSpec, title_size
from render.layout.model import LayoutResult, PageLayout, PlacedBlock


def write_html(layout: LayoutResult, path: Path) -> Path:
    path = Path(path)
    flag = "早报" if layout.kind == "am" else "晚报"
    pages = "\n".join(_page_html(layout, i) for i in range(layout.n_pages))
    warn = ""
    if layout.warnings:
        warn = "<div class='warnings'>" + "<br>".join(html.escape(w) for w in layout.warnings) + "</div>"
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>渔网{flag} · {html.escape(layout.edition_id)}</title>
<style>
  @page {{ size: A3 portrait; margin: 0; }}
  html, body {{ margin: 0; padding: 0; background: #ddd; }}
  body {{ font-family: "Songti SC", "STSong", "Noto Serif SC", "Source Han Serif SC", serif; color: #111; }}
  .wrap {{ padding: 12px 0 32px; }}
  .warnings {{ max-width: 297mm; margin: 0 auto 10px; color: #8B1E1E; font-size: 12px; }}
  .page {{
    width: 297mm; height: 420mm; margin: 0 auto 16px; position: relative;
    background: #fff; overflow: hidden; box-shadow: 0 1px 8px rgba(0,0,0,.12);
    page-break-after: always;
  }}
  .block {{ position: absolute; box-sizing: border-box; overflow: hidden; }}
  .story, .index, .placeholder {{ background: transparent; border: 0; }}
  .kicker {{ font-size: 6.6pt; letter-spacing: .12em; color: #444; position: absolute; }}
  .title {{ font-weight: 700; line-height: 1.12; position: absolute; }}
  .byline {{ color: #444; font-size: 6.6pt; position: absolute; }}
  .body {{
    font-size: 8.15pt; line-height: 1.30; position: absolute;
    text-align: justify; hyphens: auto; overflow: hidden;
    column-gap: 2.2mm; column-rule: 0.15mm solid #222;
  }}
  .jump {{ text-align: right; font-size: 7pt; position: absolute; right: 1.4mm; bottom: 0.6mm; }}
  .rule-v, .rule-h {{ position: absolute; background: #222; pointer-events: none; }}
  .photo {{ background: #f0f0f0; color: #444; display: flex; align-items: center;
            justify-content: center; font-size: 8pt; overflow: hidden; position: absolute;
            border: 0.15mm solid #222; }}
  .photo img {{ width: 100%; height: 100%; object-fit: contain; }}
  .masthead, .folio {{ color: #111; background: #fff; }}
  .mast-name {{ font-size: 42pt; font-weight: 700; text-align: center; letter-spacing: .45em;
               border-top: 0.45mm solid #111; padding-top: 2mm; }}
  .mast-flag {{ position: absolute; right: 0; top: 8mm; font-size: 11pt; }}
  .mast-meta {{ font-size: 7.4pt; color: #444; display: flex; justify-content: space-between;
               border-top: 0.28mm solid #111; border-bottom: 0.15mm solid #111; padding: 1mm 0; }}
  .mast-lede {{ font-size: 8.6pt; line-height: 1.28; margin-top: 1.5mm; }}
  .folio-line {{ font-size: 11pt; border-top: 0.28mm solid #111; border-bottom: 0.15mm solid #111;
                padding: 1.5mm 0; }}
  .inside {{ border: 0.25mm solid #111; }}
  .inside-head {{ background: #111; color: #fff; text-align: center; font-size: 9.5pt;
                 letter-spacing: .2em; padding: 1.2mm 0; }}
  .inside-item {{ padding: 1.2mm 1.6mm 0; font-size: 7.6pt; }}
  .inside-item .k {{ font-size: 6.2pt; color: #444; display: block; }}
  .inside-item .p {{ float: right; }}
  .footer {{ position: absolute; font-size: 7pt; color: #444; border-top: 0.2mm solid #111; }}
  @media print {{
    body {{ background: #fff; }}
    .wrap {{ padding: 0; }}
    .page {{ margin: 0; box-shadow: none; }}
    .warnings {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="wrap">
{warn}
{pages}
</div>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


def _page_html(layout: LayoutResult, idx: int) -> str:
    page = layout.pages[idx]
    types = TypeSpec.for_kind(layout.kind)
    parts = [f'<section class="page" data-page="{idx + 1}">']
    for b in page.blocks:
        parts.append(_block_html(layout, b, types))
    parts.extend(_gutter_html(page))
    foot = layout.geom.footer_rect()
    flag = "早报" if layout.kind == "am" else "晚报"
    parts.append(
        _abs_div(
            "footer",
            foot,
            html.escape(
                f"渔网{flag}  {layout.edition_id}  ·  第 {idx + 1} 版 / 共 {layout.n_pages} 版"
            ),
        )
    )
    parts.append("</section>")
    return "\n".join(parts)


def _gutter_html(page: PageLayout) -> list[str]:
    """相邻稿件之间的细线,对应 PDF 的 _draw_gutters。"""
    bits: list[str] = []
    blocks = [b for b in page.blocks if b.kind not in ("masthead", "folio")]
    for i, a in enumerate(blocks):
        for b in blocks[i + 1 :]:
            if a.cells.c + a.cells.w == b.cells.c or b.cells.c + b.cells.w == a.cells.c:
                left, right = (a, b) if a.cells.c < b.cells.c else (b, a)
                y1 = max(left.mm.y, right.mm.y)
                y2 = min(left.mm.bottom, right.mm.bottom)
                if y2 - y1 > 4:
                    x = (left.mm.right + right.mm.x) / 2
                    bits.append(
                        f'<div class="rule-v" style="left:{x:.2f}mm;top:{y1:.2f}mm;'
                        f'width:0.18mm;height:{y2 - y1:.2f}mm"></div>'
                    )
            if a.cells.r + a.cells.h == b.cells.r or b.cells.r + b.cells.h == a.cells.r:
                top, bot = (a, b) if a.cells.r < b.cells.r else (b, a)
                x1 = max(top.mm.x, bot.mm.x)
                x2 = min(top.mm.right, bot.mm.right)
                if x2 - x1 > 4:
                    y = (top.mm.bottom + bot.mm.y) / 2
                    bits.append(
                        f'<div class="rule-h" style="left:{x1:.2f}mm;top:{y:.2f}mm;'
                        f'width:{x2 - x1:.2f}mm;height:0.18mm"></div>'
                    )
    return bits


def _block_html(layout: LayoutResult, b: PlacedBlock, types: TypeSpec) -> str:
    if b.kind in ("masthead", "folio"):
        if b.kind == "folio":
            inner = (
                f'<div class="folio-line">渔  网　'
                f'{"早  报" if layout.kind == "am" else "晚  报"}'
                f'<span style="float:right">{html.escape(layout.edition_id)} · 第 {b.page + 1} 版</span></div>'
            )
            return _abs_div("folio", b.mm, inner)
        lede = html.escape(layout.lede or "")
        inner = (
            f'<div class="mast-flag">{"早  报" if layout.kind == "am" else "晚  报"}</div>'
            f'<div class="mast-name">渔　　网</div>'
            f'<div class="mast-meta"><span>{html.escape(layout.edition_id)} · {layout.n_articles} 篇入版</span>'
            f"<span>FISHNET</span></div>"
            f'<div class="mast-lede">{lede}</div>'
        )
        return _abs_div("masthead", b.mm, inner)

    if b.kind == "inside":
        items = b.teasers or [("内页", "本期其余稿件见后续版面", 2)]
        bits = ['<div class="inside-head">INSIDE</div>']
        for kicker, title, page_no in items:
            bits.append(
                f'<div class="inside-item"><span class="k">{html.escape(kicker)}</span>'
                f'<span class="p">{page_no}</span>{html.escape(title[:28])}</div>'
            )
        return _abs_div("inside", b.mm, "".join(bits))

    ch = b.chunk
    kicker = (ch.article.kicker if ch else b.section) or b.section
    if ch and ch.part > 0:
        kicker = f"{kicker} · 续"
    bits: list[str] = []
    kick_box = b.title_rect or MmRect(
        b.mm.x + types.pad_mm, b.mm.y + types.pad_mm, max(10.0, b.mm.w - types.pad_mm * 2), types.kicker_bar_mm
    )
    kr = _rel(kick_box, b.mm)
    bits.append(
        f'<div class="kicker" style="left:{kr.x:.2f}mm;top:{kr.y:.2f}mm;'
        f'width:{kr.w:.2f}mm;height:{types.kicker_bar_mm:.2f}mm">{html.escape(kicker)}</div>'
    )
    if ch:
        title = ch.article.title
        if ch.part > 0:
            title = f"（上接第 {b.jump_from or '?'} 版 · {ch.article.fn or ch.article.id}）"
        ts = title_size(
            ch.article.section,
            ch.part,
            types,
            lead=ch.article.priority <= 0 and ch.article.section == "headline" and ch.part == 0,
        )
        if b.title_rect is not None:
            tr = _rel(b.title_rect.inset(t=types.kicker_bar_mm), b.mm)
            bits.append(
                f'<div class="title" style="left:{tr.x:.2f}mm;top:{tr.y:.2f}mm;'
                f'width:{tr.w:.2f}mm;height:{tr.h:.2f}mm;font-size:{ts:.1f}pt">'
                f"{html.escape(title)}</div>"
            )
        if ch.part == 0 and ch.article.byline and b.title_rect is not None:
            br = _rel(b.title_rect, b.mm)
            bits.append(
                f'<div class="byline" style="left:{br.x:.2f}mm;top:{br.y + br.h - 4:.2f}mm;'
                f'width:{br.w:.2f}mm">{html.escape(ch.article.byline)}</div>'
            )
        for ib in b.image_boxes:
            rel = _rel(ib.rect, b.mm)
            cap = html.escape(ib.image.caption or ib.image.alt or "配图预留")
            img = ""
            if ib.image.src:
                img = f'<img src="{html.escape(ib.image.src)}" alt="{cap}"/>'
            bits.append(
                f'<div class="photo" style="left:{rel.x:.2f}mm;top:{rel.y:.2f}mm;'
                f'width:{rel.w:.2f}mm;height:{rel.h:.2f}mm">{img or cap}</div>'
            )
        if ch.body and b.text_rect is not None:
            n = max(1, b.n_text_cols)
            tr = _rel(b.text_rect, b.mm)
            bits.append(
                f'<div class="body" style="left:{tr.x:.2f}mm;top:{tr.y:.2f}mm;'
                f'width:{tr.w:.2f}mm;height:{tr.h:.2f}mm;column-count:{n};'
                f'font-size:{types.body_pt:.2f}pt">{html.escape(ch.body)}</div>'
            )
        if b.jump_to:
            bits.append(f'<div class="jump">下转第 {b.jump_to} 版</div>')
    return _abs_div(b.kind, b.mm, "".join(bits))


def _rel(inner: MmRect, outer: MmRect) -> MmRect:
    return MmRect(inner.x - outer.x, inner.y - outer.y, inner.w, inner.h)


def _abs_div(cls: str, r: MmRect, inner: str, extra: str = "") -> str:
    style = (
        f"left:{r.x:.2f}mm;top:{r.y:.2f}mm;width:{r.w:.2f}mm;height:{r.h:.2f}mm;"
        + extra
    )
    return f'<div class="block {cls}" style="{style}">{inner}</div>'
