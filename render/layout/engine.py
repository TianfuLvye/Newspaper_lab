"""把文章列表装进一张张 A3 矩阵页。过长的稿切成续页矩形。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from render.layout.grid import CellRect, MmRect, PageGeom
from render.layout.images import (
    estimate_well_height_mm,
    estimate_well_width_mm,
    plan_image_slots,
    wrap_obstacles,
)
from render.layout.measure import (
    BYLINE_BAND_MM,
    TypeSpec,
    cells_for_height,
    chars_that_fit,
    column_count,
    column_rects,
    estimate_title_height_mm,
    line_height_mm,
    punch_columns,
    split_body,
    text_height_mm,
    title_size,
    wrap_text,
)
from render.layout.model import Article, Chunk, LayoutResult, PageLayout, PlacedBlock
from render.layout.pack import Skyline, no_overlaps
from render.markup import has_md_table, strip_inline_md


SECTION_ORDER_AM = [
    "headline",
    "hotlist",
    "deepread",
    "critical",
    "oral",
    "subscribe",
    "health",
]
SECTION_ORDER_PM = [
    "headline",
    "deepread",
    "critical",
    "oral",
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
        widths = [4, 3, 2]
    elif prefer == "tall":
        widths = [2, 1, 3, 4]
    else:
        widths = [2, 3, 4, 1]

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


def first_chunk_cap(
    chunk: Chunk, max_h: int, cols: int, *, page_i: int = 0
) -> tuple[int, int]:
    """头版 headline 导语限高;内页开篇和续文可以占满本版。"""
    if chunk.part > 0:
        return cols, max_h
    if page_i == 0 and chunk.article.section == "headline":
        # 4 栏头版扣掉目录栏剩 3 栏:三条导语各 3×2 刚好叠满。
        return min(3, cols), min(max_h, 2)
    return cols, max_h


def prefer_for(section: str, part: int) -> str:
    if part > 0:
        # 续文拿整块宽矩形,一跳尽量写完,不要几条「上接」并排成方柱。
        return "wide"
    if section == "headline":
        return "wide"
    if section in ("hotlist", "health"):
        return "tall"
    if section == "deepread":
        return "square"
    return "square"


def estimate_area_cells(
    chunk: Chunk, geom: PageGeom, types: TypeSpec, max_h: int, *, cap: bool = True
) -> int:
    art = chunk.article
    guess_cols = 2 if art.role == "story" else 2
    if art.section == "headline" and chunk.part == 0:
        guess_cols = min(3, geom.cols)
    guess_w = guess_cols * geom.cell_w + max(guess_cols - 1, 0) * geom.gutter
    pad = types.pad_mm * 2
    inner_w = max(20.0, guess_w - pad)

    title_h = 0.0
    if chunk.part == 0:
        ts = title_size(art.section, 0, types, lead=art.priority <= 0 and art.section == "headline")
        title_h = estimate_title_height_mm(art.title, inner_w, ts, types)
        if art.byline:
            title_h += BYLINE_BAND_MM
    else:
        # 跳页题同样占位:续文面积要把小号原题算进去,不然尾巴按「无题」估小,
        # 排出来就是题贴文、文贴边的火柴梗。
        ts = title_size(art.section, chunk.part, types)
        title_h = estimate_title_height_mm(art.title, inner_w, ts, types)

    img_h = estimate_well_height_mm(chunk.images, inner_w) if chunk.images else 0.0
    img_w = estimate_well_width_mm(chunk.images, 80.0) if chunk.images else 0.0
    text_w = inner_w if img_w <= 0 else max(24.0, inner_w - img_w - 2.0)
    n_cols = max(1, column_count(text_w))
    body_h = text_height_mm(chunk.body, text_w / n_cols, types.body_pt, types.line_ratio) / n_cols
    total_h = title_h + img_h + body_h + pad + 6.0
    rows = max(3, cells_for_height(total_h, geom))
    if cap:
        rows = min(max_h, rows)
    cols = guess_cols
    area = cols * rows
    if cap:
        return min(area, geom.cols * max_h)
    return area


def _fits_one_interior_page(
    chunk: Chunk, geom: PageGeom, types: TypeSpec, max_h: int
) -> bool:
    """预估整篇(按满页上限之前的面积)能否进一版。超一版的进 long 队列置后。"""
    area = estimate_area_cells(chunk, geom, types, max_h, cap=False)
    return area <= geom.cols * max_h


@dataclass
class _Attempt:
    block: PlacedBlock
    rest: Chunk | None
    source: Chunk
    harvested: bool = False


def _budget_slice(body: str, max_chars: int) -> tuple[str, bool]:
    """按字符安全阀裁稿。超预算时在段落/句子边界下刀,并打出 over_budget 旗标,
    让最后一块印出「本文有删节」认账——绝不在一句中间悄悄截断。"""
    if len(body) <= max_chars:
        return body, False
    cut = body.rfind("\n\n", 0, max_chars)
    if cut < max_chars * 0.5:
        sent = max(
            body.rfind(p, 0, max_chars) for p in ("。", "！", "？", "；", "!", "?")
        )
        if sent > cut:
            cut = sent + 1
    if cut < max_chars * 0.5:
        cut = max_chars
    return body[:cut].rstrip(), True


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

    chunks: list[Chunk] = []
    for a in ordered:
        body0, over = _budget_slice(a.body or "", a.max_chars)
        chunks.append(Chunk(a, body0, list(a.images[:3]), 0, over_budget=over))
    interior_h = geom.rows - 1
    front: list[Chunk] = []
    normal: list[Chunk] = []
    long_q: list[Chunk] = []
    for c in chunks:
        if c.article.section == "headline":
            front.append(c)
        elif _fits_one_interior_page(c, geom, types, interior_h):
            normal.append(c)
        else:
            long_q.append(c)
    long_ids = {c.article.id for c in long_q}
    parked: list[Chunk] = []
    seq: list[Chunk] = []
    pages: list[PageLayout] = []
    n_placeholder = sum(1 for a in articles if a.empty or a.role == "placeholder")

    safety = 0
    while (front or normal or long_q or parked or seq) and safety < 48:
        safety += 1
        page_i = len(pages)
        packer = Skyline(geom.cols, geom.rows)
        mast_h = 2 if page_i == 0 else 1
        mast_cells = CellRect(0, 0, geom.cols, mast_h)
        packer.occupy(mast_cells.c, mast_cells.r, mast_cells.w, mast_cells.h)
        reserved: list[CellRect] = [mast_cells]
        blocks: list[PlacedBlock] = [
            PlacedBlock(
                chunk=None,
                cells=mast_cells,
                mm=geom.cell_to_mm(mast_cells),
                page=page_i,
                kind="masthead" if page_i == 0 else "folio",
                section="masthead",
                article_id="",
            )
        ]
        max_h = geom.rows - mast_h
        if page_i == 0 and geom.rows - mast_h >= 4:
            # 头版最左一列给本期目录;4 栏时正文区 3 栏。
            rail = CellRect(0, mast_h, 1, geom.rows - mast_h)
            packer.occupy(rail.c, rail.r, rail.w, rail.h)
            reserved.append(rail)
            blocks.append(
                PlacedBlock(
                    chunk=None,
                    cells=rail,
                    mm=geom.cell_to_mm(rail),
                    page=page_i,
                    kind="inside",
                    section="inside",
                    article_id="inside",
                )
            )
        if page_i == 0:
            work = list(front)
            front = []
        else:
            work = []
            if parked:
                work.append(parked.pop(0))
            work.extend(seq)
            seq = []
            work.extend(front)
            front = []
            if normal:
                work.extend(normal)
                normal = []
            else:
                work.extend(long_q)
                long_q = []
        attempts: list[_Attempt] = []
        work = _place_pass(packer, list(work), attempts, geom, types, page_i, max_h)
        # 面积是一次性粗估的,装完全文的块常常虚占格子。让它们缩回真实
        # 内容大小,再用吐出来的空格补放排在后面的稿。
        for _ in range(4):
            packer, work, changed = _compact_attempts(
                packer, attempts, work, geom, types, page_i, max_h, reserved=reserved
            )
            packer = _rebuild_packer(geom, reserved, attempts)
            before = len(work)
            work = _place_pass(packer, work, attempts, geom, types, page_i, max_h)
            if not changed and len(work) == before:
                break
        _grow_into_holes(packer, attempts, geom, types, page_i, max_h)
        leftover = work
        for att in attempts:
            if att.rest is not None:
                rest = att.rest
                att.rest = None
                att.harvested = True
                if page_i == 0:
                    parked.append(rest)
                else:
                    seq.append(rest)
            blocks.append(att.block)

        used_cells = [b.cells for b in blocks]
        if not no_overlaps(used_cells):
            warnings.append(f"page {page_i + 1} overlap (should not happen)")

        pages.append(PageLayout(index=page_i, geom=geom, blocks=blocks))
        content_placed = any(b.kind in ("story", "index", "placeholder") for b in blocks)
        if leftover and not content_placed:
            dropped = leftover.pop(0)
            warnings.append(f"无法为 {dropped.article.id} 分配格子,已跳过")
        for c in leftover:
            if c.part > 0:
                if c.article.section == "headline":
                    parked.insert(0, c)
                else:
                    seq.append(c)
            elif c.article.section == "headline":
                front.append(c)
            elif c.article.id in long_ids:
                long_q.append(c)
            else:
                normal.append(c)

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


def _hole_shapes(hole: CellRect, cap_w: int, cap_h: int) -> list[tuple[int, int]]:
    """对着一个洞列出可放的矩形:先铺满,再变矮/变窄留下余洞。"""
    w = min(hole.w, cap_w)
    h = min(hole.h, cap_h)
    if w < 1 or h < 2:
        return []
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for sw, sh in (
        [(w, h)]
        + [(w, hh) for hh in range(h, 1, -1)]
        + [(ww, h) for ww in range(w, 0, -1)]
        + [(ww, hh) for ww in range(w, 0, -1) for hh in range(h, 1, -1)]
    ):
        key = (sw, sh)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _fit_chunk_in_hole(
    chunk: Chunk,
    hole: CellRect,
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
) -> tuple[int, int, int, CellRect, PlacedBlock, Chunk | None] | None:
    """看这篇能不能进这个洞。不占格。

    返回 (优先级, 次键, 第三键, cell, block, rest)。
    续文写得完就收块;写不完才铺满。短稿能写完就进碎洞。
    """
    if page_i == 0:
        if chunk.part > 0 or chunk.article.section != "headline":
            return None
    cap_w, cap_h = first_chunk_cap(chunk, max_h, geom.cols, page_i=page_i)
    best: tuple[int, int, int, CellRect, PlacedBlock, Chunk | None] | None = None
    for sw, sh in _hole_shapes(hole, cap_w, cap_h):
        cell = CellRect(hole.c, hole.r, sw, sh)
        block, rest = _materialize(chunk, cell, geom, types, page_i)
        finishes = rest is None
        if sw < 2 and not chunk.article.empty:
            # 1 栏(1/4 页宽)只收能写完的短讯/续尾,不许开篇下转成长腿。
            if not finishes or sh > 3:
                continue
        if chunk.article.role == "index" and sw < 2:
            continue
        if not chunk.article.empty and sh < 2:
            continue
        is_cont = chunk.part > 0
        is_filler = chunk.article.empty or chunk.article.role == "index"
        if (
            not is_cont
            and not is_filler
            and chunk.article.role == "story"
            and page_i > 0
            and not finishes
            and (sw * sh < 8 or sh < 3)
        ):
            # 碎洞只收能一版写完的短稿;满宽两行(4×2)以上才允许开篇下转。
            continue
        waste = hole.area - sw * sh
        if is_cont:
            if finishes:
                # 写得完就收块,但别收成 4×2 横条压在下一篇头顶。
                rank = (0, 0, sw * sh + (80 if sh < 3 else 0))
            else:
                rank = (0, 1, waste)
        elif chunk.article.empty:
            rank = (3, 0, waste)
        elif finishes:
            rank = (1, 0, sw * sh)
        else:
            rank = (2, 0, waste)
        cand = (*rank, cell, block, rest)
        if best is None or cand[:3] < best[:3]:
            best = cand
    return best


def _place_pass(
    packer: Skyline,
    work: list[Chunk],
    attempts: list[_Attempt],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
    *,
    chain: bool = False,
) -> list[Chunk]:
    """hole-first:对着最大空矩形派稿。续文写得完就收块;短稿能写完就进碎洞。

    同一篇稿每版至多一块。
    """
    del chain
    progressed = True
    while progressed:
        progressed = False
        placed = {a.block.article_id for a in attempts}
        holes = sorted(
            packer.free_rects(), key=lambda h: (-h.area, h.r, h.c)
        )
        for hole in holes:
            if hole.area < 2:
                continue
            best: tuple[int, int, int, int, CellRect, PlacedBlock, Chunk | None, Chunk] | None = None
            for i, ch in enumerate(work):
                if ch.article.id in placed:
                    continue
                fit = _fit_chunk_in_hole(
                    ch, hole, geom, types, page_i, max_h
                )
                if fit is None:
                    continue
                pri, tie, waste, cell, block, rest = fit
                cand = (pri, tie, waste, i, cell, block, rest, ch)
                if best is None or cand[:4] < best[:4]:
                    best = cand
            if best is None:
                continue
            _pri, _tie, _w, _i, cell, block, rest, ch = best
            packer.occupy(cell.c, cell.r, cell.w, cell.h)
            attempts.append(_Attempt(block, rest, ch))
            work = [c for c in work if c.article.id != ch.article.id]
            progressed = True
            break
    return work


def _rebuild_packer(
    geom: PageGeom, reserved: list[CellRect], attempts: list[_Attempt]
) -> Skyline:
    """收缩后空格变了,按最新格子重建天际线。"""
    p = Skyline(geom.cols, geom.rows)
    for cell in reserved:
        p.occupy(cell.c, cell.r, cell.w, cell.h)
    for att in attempts:
        cell = att.block.cells
        p.occupy(cell.c, cell.r, cell.w, cell.h)
    return p


def _compact_attempts(
    packer: Skyline,
    attempts: list[_Attempt],
    work: list[Chunk],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
    *,
    reserved: list[CellRect],
    chain: bool = False,
) -> tuple[Skyline, list[Chunk], bool]:
    """装完全文的块把虚占的格子吐出来(缩 1 栏宽或 1 行高,直到再缩就掉字)。

    只动「字已装完」的块:续文还在或已被收走的块,缩了会把已排版的尾巴
    截出一截新续文,和后面那块对不上。占位块故意撑场面,也不动。
    每次收缩立刻重建自由区并补放排在后面的稿——腾出的洞别等整轮结束,
    中途就可能被用掉。
    """
    changed = False
    for att in attempts:
        ch = att.block.chunk
        if ch is None or att.rest is not None or att.harvested:
            continue
        if ch.body != att.source.body or att.source.article.empty:
            continue
        min_w = 1 if att.source.article.role == "story" else 2
        while True:
            cell = att.block.cells
            shrunk: PlacedBlock | None = None
            for trial in (
                CellRect(cell.c, cell.r, cell.w - 1, cell.h),
                CellRect(cell.c + 1, cell.r, cell.w - 1, cell.h),
                CellRect(cell.c, cell.r, cell.w, cell.h - 1),
                CellRect(cell.c, cell.r + 1, cell.w, cell.h - 1),
            ):
                if (
                    trial.w < min_w
                    or (trial.w == 1 and trial.h > 3)
                    or trial.h < 2
                    or trial.c < 0
                    or trial.r < 0
                    or trial.c + trial.w > geom.cols
                    or trial.r + trial.h > geom.rows
                    or (trial.w >= 2 and trial.h > 2 * trial.w)
                ):
                    continue
                block, rest = _materialize(att.source, trial, geom, types, page_i)
                if rest is not None or block.chunk is None:
                    continue
                if block.chunk.body != att.source.body:
                    continue
                if len(block.image_boxes) < len(att.block.image_boxes):
                    continue
                shrunk = block
                break
            if shrunk is None:
                break
            prev = att.block
            n_before = len(attempts)
            att.block = shrunk
            packer = _rebuild_packer(geom, reserved, attempts)
            if work:
                work = _place_pass(
                    packer, work, attempts, geom, types, page_i, max_h, chain=chain
                )
            if len(attempts) == n_before:
                freed = prev.cells.area - shrunk.cells.area
                if freed >= 8:
                    # 吐出的大洞没人进,缩回去。
                    att.block = prev
                    packer = _rebuild_packer(geom, reserved, attempts)
                    break
                # 小缝没人要也缩:图下文留白撑着 2×3,比页底空 1 行更难看。
            changed = True
    return packer, work, changed


def _grow_into_holes(
    packer: Skyline,
    attempts: list[_Attempt],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
) -> None:
    """把相邻空格吸进已放上的稿,消灭 1 栏缝和页底空洞。

    不突破 first_chunk_cap:头版导语吸满整页就又变回「全员下转第 2 版」。
    """
    changed = True
    guard = 0
    while changed and guard < 36:
        guard += 1
        changed = False
        for att in attempts:
            cell = att.block.cells
            cap_w, cap_h = first_chunk_cap(
                att.source, max_h, geom.cols, page_i=page_i
            )
            grew: CellRect | None = None
            if att.rest is None:
                # 已经写完的稿不再吸格:短讯吸回 2 栏、图下文留白会原样回来。
                continue
            elif (
                cell.w < cap_w
                and packer.region_free(cell.c + cell.w, cell.r, 1, cell.h)
            ):
                packer.occupy(cell.c + cell.w, cell.r, 1, cell.h)
                grew = CellRect(cell.c, cell.r, cell.w + 1, cell.h)
            elif (
                cell.h < cap_h
                and packer.region_free(cell.c, cell.r + cell.h, cell.w, 1)
            ):
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
    images = list(chunk.images)

    # 标题是占位元素:按自然高度参与版面计算,绝不压顶裁剪。
    # 装不下的正文走续文;标题永远完整示人。
    if chunk.part == 0:
        ts = title_size(
            art.section,
            0,
            types,
            lead=art.priority <= 0 and art.section == "headline",
        )
        th = estimate_title_height_mm(art.title or art.kicker or " ", inner.w, ts, types)
        if art.byline:
            th += BYLINE_BAND_MM
    else:
        # 续文也印原题(小号跳页题):题面才是指路牌,「上接第N版」由 kicker 携带
        ts = title_size(art.section, chunk.part, types)
        th = estimate_title_height_mm(art.title or " ", inner.w, ts, types)
    th = min(th, max(6.0, inner.h - line_height_mm(types.body_pt, types.line_ratio)))
    title_rect, body_space = inner.split_top(th)

    plan = plan_image_slots(body_space, images)
    text_rect = plan.text_rect or body_space
    overflow_imgs = list(plan.overflow_images)
    obstacles = wrap_obstacles(plan.well, plan.image_boxes)

    n_cols = column_count(text_rect.w, mm.h) if text_rect else 1
    if text_rect and text_rect.w >= 80:
        n_cols = max(n_cols, 2)
    jump_reserve = 4.2
    table_flow = has_md_table(chunk.body) and bool(obstacles)
    if table_flow:
        # 表格要通栏:绕排让位,正文从图井下方整块排,和以前的上下型一样。
        y0 = max(o.bottom for o in obstacles) + 1.2
        y0 = min(y0, text_rect.bottom)
        flow = MmRect(text_rect.x, y0, text_rect.w, max(0.0, text_rect.bottom - y0))
        n_cols = column_count(flow.w, mm.h) if flow.w > 1 else 1
        fit_h = max(0.0, flow.h - jump_reserve)
        n_fit = chars_that_fit(chunk.body, flow.w, fit_h, types.body_pt, types.line_ratio)
        text_rect = flow
    else:
        punched = punch_columns(text_rect, obstacles, n_cols)
        fit_cols = [
            MmRect(c.x, c.y, c.w, max(0.0, c.h - jump_reserve)) for c in punched
        ]
        n_fit = chars_that_fit(
            chunk.body,
            text_rect.w,
            text_rect.h,
            types.body_pt,
            types.line_ratio,
            col_rects=fit_cols,
        )
    if n_fit <= 0 and chunk.body:
        n_fit = min(80, len(chunk.body))
    head, tail = split_body(chunk.body, n_fit)
    if tail and len(tail) < 48:
        head, tail = chunk.body, ""

    # 字少就少切栏,但仍铺满原正文区:裁掉右边等于在稿框里留一条空列。
    # 宽块至少两栏,避免 209mm 导语收成一条通栏博客。
    if text_rect and head and not plan.image_boxes:
        cols = column_rects(text_rect, n_cols)
        col_w = cols[0].w
        n_lines = len(wrap_text(strip_inline_md(head), col_w, types.body_pt))
        fit_h = max(0.0, text_rect.h - jump_reserve)
        per = max(1, int(fit_h / line_height_mm(types.body_pt, types.line_ratio)))
        n_need = max(1, math.ceil(n_lines / per)) if n_lines else 1
        floor = 2 if text_rect.w >= 80 else 1
        n_cols = max(n_cols, floor)
        if n_need < n_cols:
            n_cols = max(n_need, floor)
            n_cols = min(n_cols, len(cols))

    truncated = False
    rest: Chunk | None = None
    if tail or overflow_imgs:
        # 版面没有「页数预算」:稿子过了字符安全阀就该全文见报,一页接一页
        # 排完为止。拿版面临时砍稿等于让引擎替编辑做丢稿决定。
        rest = Chunk(
            article=art,
            body=tail,
            images=overflow_imgs,
            part=chunk.part + 1,
            truncated=False,
            over_budget=chunk.over_budget,
        )
    elif chunk.over_budget:
        # 字符安全阀在段落边界裁过稿:印「有删节」认账——报纸上唯一诚实的
        # 省略方式。绝不印「见 items/」这种拿文件路径当报缝的鬼话。
        truncated = True
        head = head.rstrip() + "\n\n（本文有删节）"

    kind = "placeholder" if art.empty else ("index" if art.role == "index" else "story")
    block = PlacedBlock(
        chunk=Chunk(art, head, list(images), chunk.part, truncated),
        cells=cell,
        mm=mm,
        page=page_i,
        kind=kind,
        title_rect=title_rect,
        text_rect=text_rect,
        image_boxes=plan.image_boxes,
        well=plan.well,
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
        if nxt is None:
            continue
        # 续文永远要知道自己从哪来:跨版写「上接第 N 版」,同版写「上接本版」,
        # 由渲染层按 jump_from 与本页页码的关系措辞。下转只跨页才写。
        nxt.jump_from = b.page + 1
        if nxt.page != b.page:
            b.jump_to = nxt.page + 1


def _fill_inside(pages: list[PageLayout]) -> None:
    """用内页标题+页码填头版 Inside。"""
    if not pages:
        return
    first: dict[str, tuple[int, str, str, int]] = {}
    for p in pages:
        for b in p.blocks:
            if b.chunk is None or b.kind in ("masthead", "folio", "inside"):
                continue
            if b.article_id not in first:
                art = b.chunk.article
                title = art.title
                kicker = art.kicker or b.section
                # 字数按整稿正文去空白统计:目录要给读者一个「这篇有多长」的预期
                n_chars = len("".join((art.body or "").split()))
                first[b.article_id] = (b.page, kicker, title, n_chars)
    # 目录栏登的是「本期全部稿件的首发页码+全文字数」(含头版稿,页码标 1),
    # 按版面顺序从上到下读;不是原来那种只指内页的 INSIDE 横条。
    teasers: list[tuple[str, str, int, int]] = []
    for _aid, (pg, kicker, title, n_chars) in first.items():
        teasers.append((kicker, title, pg + 1, n_chars))
    for b in pages[0].blocks:
        if b.kind == "inside":
            # 目录题印全文+字数,条目高度按真实折行累计:栏装满为止,
            # 绝不再拿固定 18 字刀锯题面。
            inner_w = max(20.0, b.mm.w - 4.4)
            y = 7.0  # 黑条 5.4 + 栏顶留白 1.6
            picked: list[tuple[str, str, int, int]] = []
            for t in teasers:
                _kicker, title, _pg, n_chars = t
                label = f"{title} // {n_chars} 字" if n_chars else title
                n_lines = max(1, len(wrap_text(label or " ", inner_w, 7.0)))
                item_h = 3.2 + n_lines * line_height_mm(7.0, 1.42) + 1.7
                if picked and y + item_h > b.mm.h - 2.0:
                    break
                picked.append(t)
                y += item_h
            b.teasers = picked
            break
