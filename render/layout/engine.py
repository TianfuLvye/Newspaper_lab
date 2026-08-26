"""把文章列表装进一张张 A3 矩阵页。过长的稿切成续页矩形。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from render.layout.grid import CellRect, MmRect, PageGeom
from render.layout.images import estimate_well_height_mm, estimate_well_width_mm, plan_image_slots
from render.layout.measure import (
    TypeSpec,
    cells_for_height,
    chars_that_fit,
    column_count,
    column_rects,
    estimate_title_height_mm,
    line_height_mm,
    split_body,
    text_height_mm,
    title_size,
    wrap_text,
)
from render.layout.model import Article, Chunk, LayoutResult, PageLayout, PlacedBlock
from render.layout.pack import MaxRects, no_overlaps
from render.markup import strip_inline_md


SECTION_ORDER_AM = [
    "headline",
    "hotlist",
    "deepread",
    "critical",
    "subscribe",
    "health",
]
SECTION_ORDER_PM = [
    "headline",
    "deepread",
    "critical",
    "subscribe",
    "hotlist",
    "health",
]


def density_mode(articles: list[Article]) -> str:
    n = sum(1 for a in articles if a.role == "story" and not a.empty)
    if n >= 30:
        return "full"
    if n >= 10:
        return "compact"
    if n >= 3:
        return "briefing"
    return "thin"


def order_articles(articles: list[Article], kind: str, density: str) -> list[Article]:
    seq = list(SECTION_ORDER_PM if kind == "pm" else SECTION_ORDER_AM)
    if density in ("briefing", "thin"):
        # 内容很少时把体检提前,空版面比假装满版更伤
        seq = ["health"] + [s for s in seq if s != "health"]
    rank = {name: i for i, name in enumerate(seq)}
    return sorted(
        articles,
        key=lambda a: (rank.get(a.section, 50), a.priority, a.id),
    )


def candidate_shapes(
    area: int,
    *,
    max_w: int,
    max_h: int,
    prefer: str,
    min_h: int = 3,
) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []

    def add(w: int, h: int) -> None:
        w = max(1, min(int(w), max_w))
        h = max(2, min(int(h), max_h))
        if w * h < area:
            h = min(max_h, max(h, math.ceil(area / w)))
        if w * h < area:
            w = min(max_w, max(w, math.ceil(area / max(h, 1))))
        if w > max_w or h > max_h:
            return
        if w * h < min(area, max_w * max_h) and not (w == max_w and h == max_h):
            return
        key = (w, h)
        if key not in seen:
            seen.add(key)
            out.append(key)

    if prefer == "wide":
        widths = [6, 5, 4, 3, 2]
    elif prefer == "tall":
        widths = [2, 1, 3, 4]
    else:
        widths = [3, 2, 4, 5, 6, 1]

    floor = max(2, min_h)
    for w in widths:
        h = max(floor, math.ceil(area / max(w, 1)))
        add(w, h)
        add(w, h + 1)
        add(w, h + 2)
        add(w, max(floor, h - 1))
    add(max_w, min(max_h, max(floor, math.ceil(area / max(max_w, 1)))))
    add(min(max_w, 2), min(max_h, 8))
    return out


def first_chunk_cap(chunk: Chunk, max_h: int, cols: int) -> tuple[int, int]:
    """头版/首截不要吞掉整页。报纸惯例是导语一块、其余下转。"""
    if chunk.part > 0:
        return cols, max_h
    sec = chunk.article.section
    if chunk.article.role == "index" or sec in ("hotlist", "health"):
        if sec == "hotlist":
            return 2, max_h
        return cols, min(max_h, 10)
    if sec == "headline" and chunk.article.priority <= 0:
        return min(6, cols), min(max_h, 4)
    if sec == "headline":
        return 3, min(max_h, 6)
    if sec == "deepread":
        return 3, min(max_h, 6)
    return 3, min(max_h, 5)


def prefer_for(section: str, part: int) -> str:
    if part > 0:
        return "wide"
    if section == "headline":
        return "wide"
    if section in ("hotlist", "health"):
        return "tall"
    if section == "deepread":
        return "square"
    return "square"


def estimate_area_cells(chunk: Chunk, geom: PageGeom, types: TypeSpec, max_h: int) -> int:
    art = chunk.article
    guess_cols = 3 if art.role == "story" else 2
    if art.section == "headline" and chunk.part == 0:
        guess_cols = 4 if chunk.article.priority <= 1 else 3
    guess_w = guess_cols * geom.cell_w + max(guess_cols - 1, 0) * geom.gutter
    pad = types.pad_mm * 2
    inner_w = max(20.0, guess_w - pad)

    title_h = 0.0
    if chunk.part == 0:
        ts = title_size(art.section, 0, types, lead=art.priority <= 0 and art.section == "headline")
        title_h = estimate_title_height_mm(art.title, inner_w, ts, types)
        if art.byline:
            title_h += 4.2

    img_h = estimate_well_height_mm(chunk.images, inner_w) if chunk.part == 0 else 0.0
    img_w = estimate_well_width_mm(chunk.images, 80.0) if chunk.part == 0 else 0.0
    text_w = inner_w if img_w <= 0 else max(24.0, inner_w - img_w - 2.0)
    n_cols = max(1, column_count(text_w))
    body_h = text_height_mm(chunk.body, text_w / n_cols, types.body_pt, types.line_ratio) / n_cols
    total_h = title_h + img_h + body_h + pad + 6.0
    rows = min(max_h, max(3, cells_for_height(total_h, geom)))
    cols = guess_cols
    area = cols * rows
    return min(area, geom.cols * max_h)


@dataclass
class _Attempt:
    block: PlacedBlock
    rest: Chunk | None
    source: Chunk


def layout_edition(
    articles: list[Article],
    *,
    kind: str,
    edition_id: str,
    lede: str = "",
    geom: PageGeom | None = None,
) -> LayoutResult:
    geom = geom or PageGeom()
    types = TypeSpec.for_kind(kind)
    density = density_mode(articles)
    ordered = order_articles(articles, kind, density)
    warnings: list[str] = []
    if density == "thin":
        warnings.append("本期稿件极少,已把体检提前,并保持空版可见,不拿低分稿凑版。")
    elif density == "briefing":
        warnings.append("本期偏薄,已合并留白,空栏目仍印占位。")

    queue = [
        Chunk(a, (a.body or "")[: a.max_chars], list(a.images[:3]), 0)
        for a in ordered
    ]
    pages: list[PageLayout] = []
    n_placeholder = sum(1 for a in articles if a.empty or a.role == "placeholder")

    safety = 0
    while queue and safety < 48:
        safety += 1
        page_i = len(pages)
        packer = MaxRects(geom.cols, geom.rows)
        mast_h = 2 if page_i == 0 else 1
        packer.occupy(0, 0, geom.cols, mast_h)
        blocks: list[PlacedBlock] = [
            PlacedBlock(
                chunk=None,
                cells=CellRect(0, 0, geom.cols, mast_h),
                mm=geom.cell_to_mm(CellRect(0, 0, geom.cols, mast_h)),
                page=page_i,
                kind="masthead" if page_i == 0 else "folio",
                section="masthead",
                article_id="",
            )
        ]
        max_h = geom.rows - mast_h
        if page_i == 0 and geom.rows - mast_h >= 6:
            # 通栏 INSIDE 横条贴在页底,不占右下角——右下角那块会把 F02/F03 撕出 1 栏空洞。
            band_h = 2
            ic = CellRect(0, geom.rows - band_h, geom.cols, band_h)
            packer.occupy(ic.c, ic.r, ic.w, ic.h)
            max_h = geom.rows - mast_h - band_h
            blocks.append(
                PlacedBlock(
                    chunk=None,
                    cells=ic,
                    mm=geom.cell_to_mm(ic),
                    page=page_i,
                    kind="inside",
                    section="inside",
                    article_id="inside",
                )
            )
        attempts: list[_Attempt] = []
        progressed = True
        work = list(queue)
        while progressed:
            progressed = False
            nxt: list[Chunk] = []
            for ch in work:
                attempt = _try_place(packer, ch, geom, types, page_i, max_h)
                if attempt is None:
                    nxt.append(ch)
                    continue
                attempts.append(attempt)
                progressed = True
            work = nxt
        _grow_into_holes(packer, attempts, geom, types, page_i)
        rest_work = [att.rest for att in attempts if att.rest is not None]
        for att in attempts:
            att.rest = None
        progressed = True
        work = rest_work + work
        while progressed:
            progressed = False
            nxt = []
            for ch in work:
                attempt = _try_place(packer, ch, geom, types, page_i, max_h)
                if attempt is None:
                    nxt.append(ch)
                    continue
                attempts.append(attempt)
                progressed = True
                if attempt.rest is not None:
                    nxt.append(attempt.rest)
            work = nxt
        leftover = work
        for att in attempts:
            blocks.append(att.block)

        used_cells = [b.cells for b in blocks]
        if not no_overlaps(used_cells):
            warnings.append(f"page {page_i + 1} overlap (should not happen)")

        pages.append(PageLayout(index=page_i, geom=geom, blocks=blocks))
        content_placed = any(b.kind in ("story", "index", "placeholder") for b in blocks)
        if leftover == queue and not content_placed:
            dropped = leftover.pop(0)
            warnings.append(f"无法为 {dropped.article.id} 分配格子,已跳过")
            queue = leftover
            continue
        queue = leftover

    _resolve_jumps(pages)
    _fill_inside(pages)
    return LayoutResult(
        kind=kind,
        edition_id=edition_id,
        lede=lede,
        geom=geom,
        pages=pages,
        density=density,
        warnings=warnings,
        n_articles=len(articles),
        n_placeholders=n_placeholder,
    )


def _try_place(
    packer: MaxRects,
    chunk: Chunk,
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
) -> _Attempt | None:
    if chunk.part > 0 and page_i == 0:
        # 头版只放导语,续文下转内页——华尔街日报也是这样
        return None
    area = estimate_area_cells(chunk, geom, types, max_h)
    prefer = prefer_for(chunk.article.section, chunk.part)
    cap_w, cap_h = first_chunk_cap(chunk, max_h, geom.cols)
    area = min(area, cap_w * cap_h)
    min_w, min_h = (2, 4) if chunk.article.role == "index" else (2, 3)
    if chunk.part > 0:
        min_h = 2
    shapes = candidate_shapes(
        area, max_w=cap_w, max_h=cap_h, prefer=prefer, min_h=min_h
    )
    if chunk.article.empty:
        min_w, min_h = 2, 2
        shapes = [(min(6, cap_w), 2), (3, 2), (2, 2)] + shapes
    shapes.append((min(min_w, cap_w), min(min_h, cap_h)))
    shapes.append((cap_w, min(cap_h, max(4, math.ceil(area / max(cap_w, 1))))))

    for w, h in shapes:
        if w > cap_w or h > cap_h or w < min_w or h < 2:
            continue
        cell = packer.place(w, h)
        if cell is None:
            continue
        block, rest = _materialize(chunk, cell, geom, types, page_i)
        return _Attempt(block, rest, chunk)
    return None


def _grow_into_holes(
    packer: MaxRects,
    attempts: list[_Attempt],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
) -> None:
    """把相邻空格吸进已放上的稿,消灭 1 栏缝和页底空洞。"""
    changed = True
    guard = 0
    while changed and guard < 36:
        guard += 1
        changed = False
        for att in attempts:
            # 已经装完的短稿再拉高,只会把几句话均摊进六栏。热榜还能多印几条。
            if att.rest is None and att.block.kind != "index":
                continue
            cell = att.block.cells
            grew: CellRect | None = None
            if packer.region_free(cell.c + cell.w, cell.r, 1, cell.h):
                packer.occupy(cell.c + cell.w, cell.r, 1, cell.h)
                grew = CellRect(cell.c, cell.r, cell.w + 1, cell.h)
            elif packer.region_free(cell.c, cell.r + cell.h, cell.w, 1):
                packer.occupy(cell.c, cell.r + cell.h, cell.w, 1)
                grew = CellRect(cell.c, cell.r, cell.w, cell.h + 1)
            if grew is None:
                continue
            block, rest = _materialize(att.source, grew, geom, types, page_i)
            att.block = block
            att.rest = rest
            changed = True


def _materialize(
    chunk: Chunk,
    cell: CellRect,
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
) -> tuple[PlacedBlock, Chunk | None]:
    mm = geom.cell_to_mm(cell)
    inner = mm.inset(types.pad_mm, types.pad_mm, types.pad_mm, types.pad_mm)
    art = chunk.article
    title_rect: MmRect | None = None
    body_space = inner
    images = list(chunk.images) if chunk.part == 0 else []

    if chunk.part == 0:
        ts = title_size(
            art.section,
            0,
            types,
            lead=art.priority <= 0 and art.section == "headline",
        )
        th = estimate_title_height_mm(art.title or art.kicker or " ", inner.w, ts, types)
        if art.byline:
            th += 4.0
        th = min(th, max(14.0, inner.h * 0.48))
        title_rect, body_space = inner.split_top(th)
    elif chunk.part > 0:
        cont_h = 8.0
        title_rect, body_space = inner.split_top(cont_h)

    plan = plan_image_slots(body_space, images)
    text_rect = plan.text_rect or body_space
    overflow_imgs = list(plan.overflow_images)

    n_cols = column_count(text_rect.w) if text_rect else 1
    jump_reserve = 4.2
    fit_h = max(0.0, text_rect.h - jump_reserve)
    n_fit = chars_that_fit(chunk.body, text_rect.w, fit_h, types.body_pt, types.line_ratio)
    if n_fit <= 0 and chunk.body:
        n_fit = min(80, len(chunk.body))
    head, tail = split_body(chunk.body, n_fit)
    if tail and len(tail) < 48:
        head, tail = chunk.body, ""

    if text_rect and head:
        cols = column_rects(text_rect, n_cols)
        col_w = cols[0].w
        n_lines = len(wrap_text(strip_inline_md(head), col_w, types.body_pt))
        per = max(1, int(fit_h / line_height_mm(types.body_pt, types.line_ratio)))
        n_need = max(1, math.ceil(n_lines / per)) if n_lines else 1
        if n_need < n_cols:
            n_cols = n_need
            used = cols[:n_cols]
            text_rect = MmRect(
                used[0].x,
                used[0].y,
                used[-1].right - used[0].x,
                used[0].h,
            )

    truncated = False
    rest: Chunk | None = None
    used_pages = chunk.part + 1
    if tail or overflow_imgs:
        if used_pages >= art.max_pages:
            truncated = True
            head = head.rstrip() + "\n\n（未完,全文见本期 items/ 或原文。）"
            tail = ""
            overflow_imgs = []
        else:
            rest = Chunk(
                article=art,
                body=tail,
                images=overflow_imgs,
                part=chunk.part + 1,
                truncated=False,
            )

    kind = "placeholder" if art.empty else ("index" if art.role == "index" else "story")
    block = PlacedBlock(
        chunk=Chunk(art, head, images if chunk.part == 0 else [], chunk.part, truncated),
        cells=cell,
        mm=mm,
        page=page_i,
        kind=kind,
        title_rect=title_rect,
        text_rect=text_rect,
        image_boxes=plan.image_boxes,
        section=art.section,
        article_id=art.id,
        n_text_cols=n_cols,
    )
    return block, rest


def _resolve_jumps(pages: list[PageLayout]) -> None:
    loc: dict[tuple[str, int], PlacedBlock] = {}
    for p in pages:
        for b in p.blocks:
            if b.chunk is None:
                continue
            loc[(b.article_id, b.chunk.part)] = b
    for (aid, part), b in loc.items():
        nxt = loc.get((aid, part + 1))
        if nxt is not None and nxt.page != b.page:
            b.jump_to = nxt.page + 1
            nxt.jump_from = b.page + 1


def _fill_inside(pages: list[PageLayout]) -> None:
    """用内页标题+页码填头版 Inside。"""
    if not pages:
        return
    first: dict[str, tuple[int, str, str]] = {}
    jumps: list[tuple[str, str, int]] = []
    for p in pages:
        for b in p.blocks:
            if b.chunk is None or b.kind in ("masthead", "folio", "inside"):
                continue
            title = b.chunk.article.title
            kicker = b.chunk.article.kicker or b.section
            if b.article_id not in first:
                first[b.article_id] = (b.page, kicker, title)
            if b.jump_to:
                jumps.append((kicker, title, b.jump_to))
    teasers: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    def add(kicker: str, title: str, page_no: int) -> None:
        key = title[:28]
        if key in seen or page_no < 2:
            return
        seen.add(key)
        teasers.append((kicker, title, page_no))

    for kicker, title, page_no in jumps:
        add(kicker, title, page_no)
    for _aid, (pg, kicker, title) in first.items():
        add(kicker, title, pg + 1)
    teasers = teasers[:7]
    for b in pages[0].blocks:
        if b.kind == "inside":
            b.teasers = teasers
            break
