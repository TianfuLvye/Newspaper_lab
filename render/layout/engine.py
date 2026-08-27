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
from render.layout.pack import MaxRects, no_overlaps
from render.markup import has_md_table, strip_inline_md


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
        # 续文按尾巴真实长度估面积、优先方柱块:几条「上接」在内页并排站,
        # 而不是每条都拉一条半空的通栏缎带。
        return "square"
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
    rows = min(max_h, max(3, cells_for_height(total_h, geom)))
    cols = guess_cols
    area = cols * rows
    return min(area, geom.cols * max_h)


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

    queue: list[Chunk] = []
    for a in ordered:
        body0, over = _budget_slice(a.body or "", a.max_chars)
        queue.append(Chunk(a, body0, list(a.images[:3]), 0, over_budget=over))
    pages: list[PageLayout] = []
    n_placeholder = sum(1 for a in articles if a.empty or a.role == "placeholder")

    safety = 0
    while queue and safety < 48:
        safety += 1
        page_i = len(pages)
        packer = MaxRects(geom.cols, geom.rows)
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
        if page_i == 0 and geom.rows - mast_h >= 6:
            # WSJ 式左栏:头版最左一列固定给本期目录(What's News 栏位),
            # 正文区变 5 栏 × 12 行,面积与原来的通栏横条相同。
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
        attempts: list[_Attempt] = []
        work = _place_pass(packer, list(queue), attempts, geom, types, page_i, max_h)
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
        _grow_into_holes(packer, attempts, geom, types, page_i)
        rest_work: list[Chunk] = []
        for att in attempts:
            if att.rest is not None:
                rest_work.append(att.rest)
                att.rest = None
                att.harvested = True
        work = _place_pass(
            packer, rest_work + work, attempts, geom, types, page_i, max_h, chain=True
        )
        # 续文按真实尾巴装完后同样可能虚占,再收一轮。
        for _ in range(4):
            packer, work, changed = _compact_attempts(
                packer, attempts, work, geom, types, page_i, max_h,
                reserved=reserved, chain=True,
            )
            packer = _rebuild_packer(geom, reserved, attempts)
            before = len(work)
            work = _place_pass(
                packer, work, attempts, geom, types, page_i, max_h, chain=True
            )
            if not changed and len(work) == before:
                break
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
    # 一栏块合法,但只能矮(短讯/续尾):长腿细栏会像博客侧栏,不像报纸。
    min_w, min_h = (2, 4) if chunk.article.role == "index" else (1, 3)
    if chunk.part > 0:
        min_h = 2
    shapes = candidate_shapes(
        area, max_w=cap_w, max_h=cap_h, prefer=prefer, min_h=min_h
    )
    if chunk.article.empty:
        min_w, min_h = 2, 2
        shapes = [(min(6, cap_w), 2), (3, 2), (2, 2)] + shapes
    # 降级阶梯:内容区被目录栏挤窄后,头条的 6×4 放不进去;按面积缩档
    # 找 5×4 / 4×4 之类的次大形状,而不是一步跌到 1 栏火柴梗。
    for decay in (0.8, 0.62, 0.5):
        sub_area = max(min_w * min_h, int(area * decay))
        if sub_area < area:
            shapes.extend(
                candidate_shapes(
                    sub_area, max_w=cap_w, max_h=cap_h, prefer=prefer, min_h=min_h
                )
            )
    shapes.append((cap_w, min(cap_h, max(4, math.ceil(area / max(cap_w, 1))))))
    shapes.append((min(min_w, cap_w), min(min_h, cap_h)))

    for w, h in shapes:
        if w > cap_w or h > cap_h or w < min_w or h < 2 or (w == 1 and h > 4):
            continue
        cell = packer.place(w, h)
        if cell is None:
            continue
        block, rest = _materialize(chunk, cell, geom, types, page_i)
        return _Attempt(block, rest, chunk)
    return None


def _place_pass(
    packer: MaxRects,
    work: list[Chunk],
    attempts: list[_Attempt],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
    *,
    chain: bool = False,
) -> list[Chunk]:
    """一轮「能放就放」,直到没有进展。chain=True 时续文尾巴当场接着排(内页)。

    同一篇稿每版至多一块:长尾巴当场连排会把一篇文章在同一版撕成
    一堆碎块——报纸不这么干,续文等下一版拿一块大的。
    """
    progressed = True
    while progressed:
        progressed = False
        nxt: list[Chunk] = []
        placed = {a.block.article_id for a in attempts}
        for ch in work:
            if ch.article.id in placed:
                nxt.append(ch)
                continue
            attempt = _try_place(packer, ch, geom, types, page_i, max_h)
            if attempt is None:
                nxt.append(ch)
                continue
            attempts.append(attempt)
            progressed = True
            if chain and attempt.rest is not None:
                nxt.append(attempt.rest)
        work = nxt
    return work


def _rebuild_packer(
    geom: PageGeom, reserved: list[CellRect], attempts: list[_Attempt]
) -> MaxRects:
    """收缩后空格变了,按最新格子重建自由区域。一页块数很少,重建最便宜。"""
    p = MaxRects(geom.cols, geom.rows)
    for cell in reserved:
        p.occupy(cell.c, cell.r, cell.w, cell.h)
    for att in attempts:
        cell = att.block.cells
        p.occupy(cell.c, cell.r, cell.w, cell.h)
    return p


def _compact_attempts(
    packer: MaxRects,
    attempts: list[_Attempt],
    work: list[Chunk],
    geom: PageGeom,
    types: TypeSpec,
    page_i: int,
    max_h: int,
    *,
    reserved: list[CellRect],
    chain: bool = False,
) -> tuple[MaxRects, list[Chunk], bool]:
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
                # 别缩出又细又长的条:h > 2w 的块像博客侧栏,裂出的缝也拼不回大洞
                if (
                    trial.w < min_w
                    or (trial.w == 1 and trial.h > 4)
                    or trial.h < 2
                    or trial.c < 0
                    or trial.r < 0
                    or trial.c + trial.w > geom.cols
                    or trial.r + trial.h > geom.rows
                    or trial.h > 2 * trial.w
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
            att.block = shrunk
            changed = True
            packer = _rebuild_packer(geom, reserved, attempts)
            if work:
                work = _place_pass(
                    packer, work, attempts, geom, types, page_i, max_h, chain=chain
                )
    return packer, work, changed


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
            # 没装完的稿才值得长:多一格就能多印几行。已经装完的稿(包括条目
            # 印完的 index)再拉高,只会把几句话均摊进更多栏、拉出一块空白框。
            if att.rest is None:
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

    n_cols = column_count(text_rect.w) if text_rect else 1
    jump_reserve = 4.2
    table_flow = has_md_table(chunk.body) and bool(obstacles)
    if table_flow:
        # 表格要通栏:绕排让位,正文从图井下方整块排,和以前的上下型一样。
        y0 = max(o.bottom for o in obstacles) + 1.2
        y0 = min(y0, text_rect.bottom)
        flow = MmRect(text_rect.x, y0, text_rect.w, max(0.0, text_rect.bottom - y0))
        n_cols = column_count(flow.w) if flow.w > 1 else 1
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

    # 字少就收栏:有图时不能把图所占的栏收掉,否则绕排对不齐。
    if text_rect and head and not plan.image_boxes:
        cols = column_rects(text_rect, n_cols)
        col_w = cols[0].w
        n_lines = len(wrap_text(strip_inline_md(head), col_w, types.body_pt))
        fit_h = max(0.0, text_rect.h - jump_reserve)
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
