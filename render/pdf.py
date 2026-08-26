"""把 LayoutResult 画成 A3 PDF。白底、细线分栏、竖栏正文——华尔街日报那一套。"""
from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.pdfgen import canvas

from render.fonts import register_pdf_fonts
from render.layout.grid import MM_PER_PT, MmRect, mm_to_pt
from render.layout.measure import (
    TypeSpec,
    char_em,
    column_count,
    column_rects,
    line_height_mm,
    title_size,
    wrap_text,
)
from render.layout.model import LayoutResult, PageLayout, PlacedBlock
from render.markup import MdTable, split_md_segments, strip_inline_md

INK = HexColor("#111111")
MUTED = HexColor("#444444")
HAIR = HexColor("#222222")


def write_pdf(layout: LayoutResult, path: Path) -> Path:
    path = Path(path)
    fonts = register_pdf_fonts()
    types = TypeSpec.for_kind(layout.kind)
    w = mm_to_pt(layout.geom.page_w)
    h = mm_to_pt(layout.geom.page_h)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setTitle(f"自动日报{('早报' if layout.kind == 'am' else '晚报')} · {layout.edition_id}")
    c.setAuthor("自动日报")
    for page in layout.pages:
        _draw_page(c, layout, page, types, fonts.body, fonts.title, h)
        c.showPage()
    c.save()
    return path


def _y(page_h_pt: float, top_mm: float) -> float:
    return page_h_pt - mm_to_pt(top_mm)


def _hline(c: canvas.Canvas, x1: float, x2: float, y_mm: float, page_h_pt: float, w: float = 0.45, color=None) -> None:
    c.setStrokeColor(color or HAIR)
    c.setLineWidth(w)
    c.line(mm_to_pt(x1), _y(page_h_pt, y_mm), mm_to_pt(x2), _y(page_h_pt, y_mm))


def _vline(c: canvas.Canvas, x_mm: float, y1: float, y2: float, page_h_pt: float, w: float = 0.35) -> None:
    c.setStrokeColor(HAIR)
    c.setLineWidth(w)
    c.line(mm_to_pt(x_mm), _y(page_h_pt, y1), mm_to_pt(x_mm), _y(page_h_pt, y2))


def _draw_page(
    c: canvas.Canvas,
    layout: LayoutResult,
    page: PageLayout,
    types: TypeSpec,
    body_font: str,
    title_font: str,
    page_h_pt: float,
) -> None:
    c.setFillColor(white)
    c.rect(0, 0, mm_to_pt(layout.geom.page_w), page_h_pt, fill=1, stroke=0)

    for block in page.blocks:
        if block.kind in ("masthead", "folio"):
            _draw_masthead(c, layout, block, types, body_font, title_font, page_h_pt)
        elif block.kind == "inside":
            _draw_inside(c, block, body_font, title_font, page_h_pt)
        else:
            _draw_article(c, block, types, body_font, title_font, page_h_pt)

    _draw_gutters(c, page, page_h_pt)

    foot = layout.geom.footer_rect()
    c.setFillColor(MUTED)
    c.setFont(body_font, 7.0)
    label = "早报" if layout.kind == "am" else "晚报"
    text = (
        f"自动日报{label}  {layout.edition_id}  ·  第 {page.index + 1} 版 / 共 {layout.n_pages} 版"
    )
    c.drawString(mm_to_pt(foot.x), _y(page_h_pt, foot.y + 4.2), text)
    _hline(c, foot.x, foot.right, foot.y, page_h_pt, 0.5)


def _draw_gutters(c: canvas.Canvas, page: PageLayout, page_h_pt: float) -> None:
    """相邻稿件之间画竖线/横线,不画外框。"""
    blocks = [b for b in page.blocks if b.kind not in ("masthead", "folio")]
    for i, a in enumerate(blocks):
        for b in blocks[i + 1 :]:
            if a.cells.c + a.cells.w == b.cells.c or b.cells.c + b.cells.w == a.cells.c:
                left, right = (a, b) if a.cells.c < b.cells.c else (b, a)
                y1 = max(left.mm.y, right.mm.y)
                y2 = min(left.mm.bottom, right.mm.bottom)
                if y2 - y1 > 4:
                    x = (left.mm.right + right.mm.x) / 2
                    _vline(c, x, y1, y2, page_h_pt, 0.32)
            if a.cells.r + a.cells.h == b.cells.r or b.cells.r + b.cells.h == a.cells.r:
                top, bot = (a, b) if a.cells.r < b.cells.r else (b, a)
                x1 = max(top.mm.x, bot.mm.x)
                x2 = min(top.mm.right, bot.mm.right)
                if x2 - x1 > 4:
                    y = (top.mm.bottom + bot.mm.y) / 2
                    _hline(c, x1, x2, y, page_h_pt, 0.32)


def _draw_masthead(
    c: canvas.Canvas,
    layout: LayoutResult,
    block: PlacedBlock,
    types: TypeSpec,
    body_font: str,
    title_font: str,
    page_h_pt: float,
) -> None:
    r = block.mm
    if block.kind == "folio":
        _hline(c, r.x, r.right, r.y + 1.2, page_h_pt, 0.7)
        c.setFillColor(INK)
        c.setFont(title_font, 11)
        flag = "早报" if layout.kind == "am" else "晚报"
        c.drawString(mm_to_pt(r.x), _y(page_h_pt, r.y + 8.5), f"自动日报 · {flag}")
        c.setFont(body_font, 8)
        c.drawRightString(
            mm_to_pt(r.right),
            _y(page_h_pt, r.y + 8.5),
            f"{layout.edition_id}  ·  第 {block.page + 1} 版",
        )
        _hline(c, r.x, r.right, r.bottom - 0.8, page_h_pt, 0.35)
        return

    _hline(c, r.x, r.right, r.y + 0.6, page_h_pt, 1.1)
    c.setFillColor(INK)
    c.setFont(title_font, 42)
    c.drawCentredString(mm_to_pt(r.x + r.w / 2), _y(page_h_pt, r.y + 18.0), "自　动　日　报")
    c.setFont(title_font, 11)
    flag = "早  报" if layout.kind == "am" else "晚  报"
    c.drawRightString(mm_to_pt(r.right), _y(page_h_pt, r.y + 18.0), flag)
    _hline(c, r.x, r.right, r.y + 22.0, page_h_pt, 0.7)
    _hline(c, r.x, r.right, r.y + 23.2, page_h_pt, 0.25)
    c.setFont(body_font, 7.4)
    c.setFillColor(MUTED)
    c.drawString(
        mm_to_pt(r.x),
        _y(page_h_pt, r.y + 26.0),
        f"{layout.edition_id}  ·  {layout.n_articles} 篇入版  ·  {layout.geom.cols} 栏",
    )
    c.drawRightString(mm_to_pt(r.right), _y(page_h_pt, r.y + 26.0), "AUTO DAILY")
    _hline(c, r.x, r.right, r.y + 28.2, page_h_pt, 0.35)
    lede = layout.lede or ""
    if lede:
        inner = MmRect(r.x, r.y + 30.0, r.w, max(8.0, r.h - 32.0))
        _draw_wrapped(
            c, lede, inner, body_font, types.lede_pt, 1.28, INK, page_h_pt, justify=False
        )
    _hline(c, r.x, r.right, r.bottom - 0.4, page_h_pt, 0.9)


def _draw_inside(
    c: canvas.Canvas,
    block: PlacedBlock,
    body_font: str,
    title_font: str,
    page_h_pt: float,
) -> None:
    r = block.mm
    rail = r.w < 100.0
    bar_h = 5.4
    c.setFillColor(INK)
    c.rect(
        mm_to_pt(r.x),
        _y(page_h_pt, r.y + bar_h),
        mm_to_pt(r.w),
        mm_to_pt(bar_h),
        fill=1,
        stroke=0,
    )
    c.setFillColor(white)
    c.setFont(title_font, 8.6)
    c.drawCentredString(
        mm_to_pt(r.x + r.w / 2), _y(page_h_pt, r.y + 3.9), "目  录" if rail else "INSIDE"
    )
    c.setStrokeColor(INK)
    c.setLineWidth(0.55)
    c.rect(
        mm_to_pt(r.x),
        _y(page_h_pt, r.bottom),
        mm_to_pt(r.w),
        mm_to_pt(r.h),
        fill=0,
        stroke=1,
    )
    items = block.teasers or [("内页", "本期其余稿件见后续版面", 2)]
    n_cols = 3 if r.w > 160 else 1
    col_w = r.w / n_cols
    body = MmRect(r.x, r.y + bar_h, r.w, max(4.0, r.h - bar_h))
    per_col = max(1, (len(items) + n_cols - 1) // n_cols)
    # 目录栏按栏高拉开行距,竖栏不再前几条挤成一团、后半截空着
    step = max(10.2, min(16.0, (body.h - 4.2) / max(1, per_col)))
    t_lim = 14 if rail else 18
    for i, (kicker, title, page_no) in enumerate(items):
        col = i // per_col
        row = i % per_col
        if col >= n_cols:
            break
        x = body.x + col * col_w
        y = body.y + 4.2 + row * step
        if y > r.bottom - 3:
            continue
        c.setFont(body_font, 6.0)
        c.setFillColor(MUTED)
        c.drawString(mm_to_pt(x + 2.0), _y(page_h_pt, y), kicker[:8])
        c.setFillColor(INK)
        c.setFont(title_font, 7.2)
        short = title[:t_lim] + ("…" if len(title) > t_lim else "")
        c.drawString(mm_to_pt(x + 2.0), _y(page_h_pt, y + 3.6), short)
        c.setFont(body_font, 7.0)
        c.drawRightString(mm_to_pt(x + col_w - 2.2), _y(page_h_pt, y + 3.6), f"{page_no}")


def _draw_article(
    c: canvas.Canvas,
    block: PlacedBlock,
    types: TypeSpec,
    body_font: str,
    title_font: str,
    page_h_pt: float,
) -> None:
    r = block.mm
    ch = block.chunk
    if ch is None:
        return

    pad = types.pad_mm
    kicker = ch.article.kicker or block.section
    if ch.part > 0:
        frm = block.jump_from
        src = f"第 {frm} 版" if frm is not None and frm != block.page + 1 else "本版"
        kicker = f"{kicker} · 上接{src}"
    kick_box = block.title_rect or MmRect(r.x + pad, r.y + pad, r.w - pad * 2, types.kicker_bar_mm)
    c.setFillColor(MUTED)
    c.setFont(body_font, types.kicker_pt)
    c.drawString(
        mm_to_pt(kick_box.x),
        _y(page_h_pt, kick_box.y + types.kicker_pt * 0.42),
        kicker,
    )

    if block.title_rect is not None:
        title = ch.article.title
        ts = title_size(
            ch.article.section,
            ch.part,
            types,
            lead=ch.article.priority <= 0 and ch.article.section == "headline" and ch.part == 0,
        )
        _draw_wrapped(
            c,
            title,
            block.title_rect.inset(t=types.kicker_bar_mm),
            title_font,
            ts,
            1.10,
            INK,
            page_h_pt,
            justify=False,
        )
        if ch.part == 0 and ch.article.byline:
            c.setFillColor(MUTED)
            c.setFont(body_font, 6.6)
            tr = block.title_rect
            c.drawString(mm_to_pt(tr.x), _y(page_h_pt, tr.bottom - 0.2), ch.article.byline[:80])

    for ib in block.image_boxes:
        ix, iy = mm_to_pt(ib.rect.x), _y(page_h_pt, ib.rect.bottom)
        iw, ih = mm_to_pt(ib.rect.w), mm_to_pt(ib.rect.h)
        drawn = False
        src = ib.image.src
        if src and Path(src).exists():
            try:
                c.drawImage(src, ix, iy, iw, ih, preserveAspectRatio=True, mask="auto")
                drawn = True
            except Exception:
                drawn = False
        if not drawn:
            c.setFillColor(HexColor("#F0F0F0"))
            c.setStrokeColor(HAIR)
            c.setLineWidth(0.3)
            c.rect(ix, iy, iw, ih, fill=1, stroke=1)
            c.setFillColor(MUTED)
            c.setFont(body_font, 7)
            cap = ib.image.caption or ib.image.alt or "配图预留"
            c.drawCentredString(ix + iw / 2, iy + ih / 2, cap[:18])

    if block.text_rect is not None and ch.body:
        _draw_flow(
            c,
            ch.body,
            block.text_rect,
            body_font,
            types.body_pt,
            types.line_ratio,
            page_h_pt,
            n_cols=block.n_text_cols or column_count(block.text_rect.w),
            title_font=title_font,
        )

    if block.jump_to:
        c.setFillColor(INK)
        c.setFont(body_font, 7.0)
        c.drawRightString(
            mm_to_pt(r.right - pad),
            _y(page_h_pt, r.bottom - 1.0),
            f"下转第 {block.jump_to} 版",
        )


def _draw_columns(
    c: canvas.Canvas,
    text: str,
    rect: MmRect,
    font: str,
    size_pt: float,
    line_ratio: float,
    page_h_pt: float,
    n_cols: int,
) -> None:
    cols = column_rects(rect, max(1, n_cols))
    col_w = cols[0].w
    lines = wrap_text(strip_inline_md(text), col_w, size_pt)
    lh = line_height_mm(size_pt, line_ratio)
    per = max(1, int(rect.h / lh))
    idx = 0
    c.setFillColor(INK)
    c.setFont(font, size_pt)
    for i, col in enumerate(cols):
        if i > 0:
            x_rule = (cols[i - 1].right + col.x) / 2
            _vline(c, x_rule, col.y + 1.0, col.bottom - 1.0, page_h_pt, 0.22)
        chunk_lines = lines[idx : idx + per]
        idx += per
        cursor = col.y + size_pt * MM_PER_PT * 0.88
        for j, line in enumerate(chunk_lines):
            if cursor > col.bottom - 0.3:
                break
            nxt_empty = j + 1 >= len(chunk_lines) or chunk_lines[j + 1] == ""
            if line:
                _draw_line(
                    c,
                    line,
                    col.x,
                    cursor,
                    col.w,
                    font,
                    size_pt,
                    page_h_pt,
                    justify=not nxt_empty,
                )
            cursor += lh


def _draw_flow(
    c: canvas.Canvas,
    text: str,
    rect: MmRect,
    font: str,
    size_pt: float,
    line_ratio: float,
    page_h_pt: float,
    n_cols: int,
    title_font: str,
) -> None:
    """正文 + 管道表格的混合流。表格通栏通排,前文在各栏间均分——
    和 HTML 的 column-fill:balance + column-span:all 同一个意思。"""
    segs = split_md_segments(text)
    if not any(kind == "table" for kind, _ in segs):
        _draw_columns(c, text, rect, font, size_pt, line_ratio, page_h_pt, n_cols)
        return
    cur = rect
    for i, (kind, seg) in enumerate(segs):
        if cur.h < 8:
            break
        if kind == "table":
            used = _draw_table(c, seg, cur, font, title_font, page_h_pt)
            cur = MmRect(cur.x, cur.y + used + 1.6, cur.w, max(0.0, cur.h - used - 1.6))
        else:
            more_table = any(k == "table" for k, _ in segs[i + 1 :])
            used = _draw_text_segment(
                c,
                str(seg),
                cur,
                font,
                size_pt,
                line_ratio,
                page_h_pt,
                n_cols,
                balance=more_table,
            )
            cur = MmRect(cur.x, cur.y + used + 1.2, cur.w, max(0.0, cur.h - used - 1.2))


def _draw_text_segment(
    c: canvas.Canvas,
    text: str,
    rect: MmRect,
    font: str,
    size_pt: float,
    line_ratio: float,
    page_h_pt: float,
    n_cols: int,
    *,
    balance: bool,
) -> float:
    """排一段纯文本,返回实际吃掉的高度。balance=True 时均分到各栏
    (后面紧跟通栏表格,免得首栏塞到底、其余栏空着)。"""
    cols = column_rects(rect, max(1, n_cols))
    col_w = cols[0].w
    lines = wrap_text(strip_inline_md(text), col_w, size_pt)
    lh = line_height_mm(size_pt, line_ratio)
    per = max(1, int(rect.h / lh))
    if balance and lines:
        per = max(1, min(per, math.ceil(len(lines) / len(cols))))
    idx = 0
    c.setFillColor(INK)
    c.setFont(font, size_pt)
    used = 0
    for i, col in enumerate(cols):
        if i > 0:
            x_rule = (cols[i - 1].right + col.x) / 2
            _vline(c, x_rule, col.y + 1.0, col.bottom - 1.0, page_h_pt, 0.22)
        chunk_lines = lines[idx : idx + per]
        idx += per
        cursor = col.y + size_pt * MM_PER_PT * 0.88
        drawn = 0
        for j, line in enumerate(chunk_lines):
            if cursor > col.bottom - 0.3:
                break
            nxt_empty = j + 1 >= len(chunk_lines) or chunk_lines[j + 1] == ""
            if line:
                _draw_line(
                    c,
                    line,
                    col.x,
                    cursor,
                    col.w,
                    font,
                    size_pt,
                    page_h_pt,
                    justify=not nxt_empty,
                )
            cursor += lh
            drawn += 1
        used = max(used, drawn)
    return used * lh


def _cell_em(text: str) -> float:
    return sum(char_em(ch) for ch in text)


def _fit_cell(text: str, width_mm: float, size_pt: float) -> str:
    if _cell_em(text) * size_pt * MM_PER_PT <= width_mm:
        return text
    while text and _cell_em(text + "…") * size_pt * MM_PER_PT > width_mm:
        text = text[:-1]
    return text + "…" if text else ""


def _draw_table(
    c: canvas.Canvas,
    tbl: MdTable,
    rect: MmRect,
    body_font: str,
    title_font: str,
    page_h_pt: float,
) -> float:
    """管道表格画成通栏细线表:无竖线、头尾粗线、行间发丝线。返回吃掉的高度。"""
    size_pt = 6.2
    row_h = line_height_mm(size_pt, 1.22) + 0.9
    n_cols = max(1, len(tbl.header))

    def cell_at(row: list[str], j: int) -> str:
        return row[j] if j < len(row) else ""

    def clean(text: str) -> str:
        return strip_inline_md(text.replace("`", "")).strip()

    nat = []
    for j in range(n_cols):
        cells = [cell_at(tbl.header, j)] + [cell_at(r, j) for r in tbl.rows]
        nat.append(max(2.0, max(_cell_em(clean(x)) for x in cells)))
    total = sum(nat)
    widths = [rect.w * w / total for w in nat]
    xs = [rect.x]
    for w in widths:
        xs.append(xs[-1] + w)

    rowline = HexColor("#c9c9c9")
    y = rect.y + 0.4
    _hline(c, rect.x, rect.right, y, page_h_pt, 0.4)
    y = _draw_table_row(
        c, tbl.header, tbl.aligns, xs, widths, y + 0.9, row_h, title_font, size_pt, page_h_pt, clean
    )
    _hline(c, rect.x, rect.right, y, page_h_pt, 0.25)
    for r in tbl.rows:
        if y + row_h > rect.bottom + 0.01:
            break
        y = _draw_table_row(
            c, r, tbl.aligns, xs, widths, y, row_h, body_font, size_pt, page_h_pt, clean
        )
        _hline(c, rect.x, rect.right, y, page_h_pt, 0.12, color=rowline)
    _hline(c, rect.x, rect.right, y, page_h_pt, 0.4)
    return y - rect.y + 0.4


def _draw_table_row(
    c: canvas.Canvas,
    cells: list[str],
    aligns: list[str],
    xs: list[float],
    widths: list[float],
    y: float,
    row_h: float,
    font: str,
    size_pt: float,
    page_h_pt: float,
    clean,
) -> float:
    c.setFont(font, size_pt)
    c.setFillColor(INK)
    baseline = y + size_pt * MM_PER_PT * 0.82 + 0.45
    for j in range(len(widths)):
        txt = _fit_cell(clean(cells[j] if j < len(cells) else ""), widths[j] - 1.4, size_pt)
        if not txt:
            continue
        a = aligns[j] if j < len(aligns) else "l"
        if a == "r":
            c.drawRightString(mm_to_pt(xs[j + 1] - 0.7), _y(page_h_pt, baseline), txt)
        elif a == "c":
            c.drawCentredString(mm_to_pt((xs[j] + xs[j + 1]) / 2), _y(page_h_pt, baseline), txt)
        else:
            c.drawString(mm_to_pt(xs[j] + 0.7), _y(page_h_pt, baseline), txt)
    return y + row_h


def _draw_line(
    c: canvas.Canvas,
    text: str,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    font: str,
    size_pt: float,
    page_h_pt: float,
    *,
    justify: bool,
) -> None:
    pdf_y = _y(page_h_pt, y_mm)
    if not justify or len(text) < 4:
        c.drawString(mm_to_pt(x_mm), pdf_y, text)
        return
    natural = sum(char_em(ch) for ch in text) * size_pt * MM_PER_PT
    extra = width_mm - natural
    if extra < 0.35:
        c.drawString(mm_to_pt(x_mm), pdf_y, text)
        return
    gap = extra / (len(text) - 1)
    cx = x_mm
    for i, ch in enumerate(text):
        c.drawString(mm_to_pt(cx), pdf_y, ch)
        cx += char_em(ch) * size_pt * MM_PER_PT + (gap if i < len(text) - 1 else 0)


def _draw_wrapped(
    c: canvas.Canvas,
    text: str,
    rect: MmRect,
    font: str,
    size_pt: float,
    line_ratio: float,
    color: Color,
    page_h_pt: float,
    *,
    justify: bool = False,
) -> None:
    if rect.w <= 1 or rect.h <= 1 or not text:
        return
    c.setFillColor(color)
    c.setFont(font, size_pt)
    lh = line_height_mm(size_pt, line_ratio)
    lines = wrap_text(text, rect.w, size_pt)
    cursor = rect.y + size_pt * MM_PER_PT * 0.90
    for j, line in enumerate(lines):
        if cursor > rect.bottom - 0.3:
            break
        if line:
            nxt_empty = j + 1 >= len(lines) or lines[j + 1] == ""
            _draw_line(
                c,
                line,
                rect.x,
                cursor,
                rect.w,
                font,
                size_pt,
                page_h_pt,
                justify=justify and not nxt_empty,
            )
        cursor += lh
