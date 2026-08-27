"""字号 ↔ 占格。装箱和 PDF 画字必须走同一套度量,否则会出现切字或大片空白。"""
from __future__ import annotations

from dataclasses import dataclass

from render.layout.grid import MM_PER_PT, MmRect, PageGeom


def char_em(ch: str) -> float:
    """相对字号的宽度。CJK ≈ 1em,拉丁半角 ≈ 0.5em。"""
    if ch in "\n\r":
        return 0.0
    if ch in " \t":
        return 0.33
    o = ord(ch)
    if o < 128:
        if ch in "ilI.,;:'!|`":
            return 0.32
        if ch in "mwMW@%":
            return 0.78
        if ch.isupper():
            return 0.62
        return 0.50
    return 1.0


def wrap_text(text: str, width_mm: float, font_size_pt: float) -> list[str]:
    if width_mm <= 1.0 or font_size_pt <= 0:
        return []
    max_em = width_mm / (font_size_pt * MM_PER_PT)
    if max_em < 1:
        max_em = 1.0
    lines: list[str] = []
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for para in raw.split("\n"):
        if para == "":
            lines.append("")
            continue
        cur = ""
        cur_em = 0.0
        for ch in para:
            w = char_em(ch)
            if cur and cur_em + w > max_em + 1e-6:
                lines.append(cur)
                cur, cur_em = ch, w
            else:
                cur += ch
                cur_em += w
        if cur:
            lines.append(cur)
    return lines


def _title_char_em(ch: str) -> float:
    """标题黑体加粗,拉丁比正文 char_em 更宽,避免 CIA 一类缩写被塞进末行。"""
    w = char_em(ch)
    if ord(ch) < 128 and (ch.isalpha() or ch in "%@&"):
        return max(w, 0.72)
    return w


def wrap_title(text: str, width_mm: float, font_size_pt: float) -> list[str]:
    """标题折行:拉丁词不拆,可用宽留 4% 给加粗/浏览器,比 wrap_text 更保守。"""
    if width_mm <= 1.0 or font_size_pt <= 0:
        return []
    max_em = (width_mm * 0.96) / (font_size_pt * MM_PER_PT)
    if max_em < 1:
        max_em = 1.0
    lines: list[str] = []
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for para in raw.split("\n"):
        if para == "":
            lines.append("")
            continue
        cur = ""
        cur_em = 0.0
        i = 0
        n = len(para)
        while i < n:
            ch = para[i]
            if ch.isascii() and ch.isalnum():
                j = i + 1
                while j < n and para[j].isascii() and para[j].isalnum():
                    j += 1
                token = para[i:j]
                tw = sum(_title_char_em(c) for c in token)
                if cur and cur_em + tw > max_em + 1e-6:
                    lines.append(cur)
                    cur, cur_em = "", 0.0
                if tw > max_em + 1e-6 and not cur:
                    # 超长拉丁词单独成行,不按字母切开(接近 CSS 对 word 的处理)
                    lines.append(token)
                    i = j
                    continue
                cur += token
                cur_em += tw
                i = j
                continue
            w = _title_char_em(ch)
            if cur and cur_em + w > max_em + 1e-6:
                lines.append(cur)
                cur, cur_em = ch, w
            else:
                cur += ch
                cur_em += w
            i += 1
        if cur:
            lines.append(cur)
    return lines


TITLE_SLACK_MM = 2.2
BYLINE_BAND_MM = 4.0


def title_wrap_line_count(title: str, width_mm: float, size_pt: float) -> int:
    """标题占几行。末行快满时加一行:黑体加粗比 1em 略宽,浏览器会再挤出一字。"""
    lines = wrap_title(title, width_mm, size_pt)
    n = len(lines)
    if n and width_mm > 1 and size_pt > 0:
        max_em = (width_mm * 0.96) / (size_pt * MM_PER_PT)
        last_em = sum(_title_char_em(ch) for ch in lines[-1])
        if last_em > max_em * 0.78:
            n += 1
    return n


def line_height_mm(font_size_pt: float, line_ratio: float) -> float:
    return font_size_pt * MM_PER_PT * line_ratio


def text_height_mm(
    text: str,
    width_mm: float,
    font_size_pt: float,
    line_ratio: float = 1.36,
) -> float:
    lines = wrap_text(text, width_mm, font_size_pt)
    return len(lines) * line_height_mm(font_size_pt, line_ratio)


COL_TARGET_MM = 41.0
COL_GUTTER_MM = 2.2


def column_count(width_mm: float) -> int:
    """一块正文区要切几条竖栏。栏宽约 41mm,接近传统报纸栏。"""
    if width_mm < 26:
        return 1
    n = max(1, int((width_mm + COL_GUTTER_MM) / (COL_TARGET_MM + COL_GUTTER_MM)))
    return min(n, 6)


def column_rects(rect: MmRect, n: int | None = None) -> list[MmRect]:
    n = n or column_count(rect.w)
    if n <= 1:
        return [rect]
    gut = COL_GUTTER_MM * (n - 1)
    cw = (rect.w - gut) / n
    if cw < 18:
        return [rect]
    return [
        MmRect(rect.x + i * (cw + COL_GUTTER_MM), rect.y, cw, rect.h)
        for i in range(n)
    ]


PUNCH_GUTTER_MM = 1.6


def punch_columns(
    body: MmRect,
    obstacles: list[MmRect],
    n_cols: int | None = None,
    *,
    gutter_mm: float = PUNCH_GUTTER_MM,
) -> list[MmRect]:
    """每栏减去与图井相交的顶段,留下一块矩形给正文。

    图顶对齐且咬在栏缝上时,被挡的栏只是起点更低、高度更短,不会变成 L 形。
    """
    cols = column_rects(body, n_cols)
    if not obstacles:
        return cols
    punched: list[MmRect] = []
    for col in cols:
        y0 = col.y
        for obs in obstacles:
            if obs.w <= 0 or obs.h <= 0:
                continue
            ox1 = obs.x - gutter_mm
            ox2 = obs.right + gutter_mm
            if ox2 <= col.x or ox1 >= col.right:
                continue
            y0 = max(y0, obs.bottom + gutter_mm)
        h = max(0.0, col.bottom - y0)
        punched.append(MmRect(col.x, y0, col.w, h))
    return punched


def column_rule_spans(
    body: MmRect,
    punched: list[MmRect],
) -> list[tuple[float, float, float]]:
    """栏间竖线:(x, y1, y2)。两侧都被图挡住时,线只画在图下方。"""
    n = len(punched)
    if n <= 1:
        return []
    rules: list[tuple[float, float, float]] = []
    for i in range(1, n):
        a, b = punched[i - 1], punched[i]
        x = (a.right + b.x) / 2
        left_from_top = a.y <= body.y + 1.2
        right_from_top = b.y <= body.y + 1.2
        if left_from_top and right_from_top:
            y1 = body.y
        elif not left_from_top and not right_from_top:
            y1 = max(a.y, b.y)
        else:
            y1 = body.y
        y2 = body.bottom
        if y2 - y1 > 3:
            rules.append((x, y1 + 1.0, y2 - 1.0))
    return rules


def split_text_by_columns(
    text: str,
    col_rects: list[MmRect],
    font_size_pt: float,
    line_ratio: float,
) -> list[str]:
    """按各栏行预算切开正文。栏与栏之间不按句号吸附,分页切口才吸附。"""
    remaining = text or ""
    parts: list[str] = []
    for i, col in enumerate(col_rects):
        if not remaining:
            parts.append("")
            continue
        if i == len(col_rects) - 1:
            parts.append(remaining)
            remaining = ""
            continue
        n = chars_that_fit(
            remaining,
            col.w,
            col.h,
            font_size_pt,
            line_ratio,
            columns=False,
            snap=False,
        )
        parts.append(remaining[:n])
        remaining = remaining[n:]
    return parts


def chars_that_fit(
    text: str,
    width_mm: float,
    height_mm: float,
    font_size_pt: float,
    line_ratio: float = 1.36,
    *,
    columns: bool = True,
    col_rects: list[MmRect] | None = None,
    snap: bool = True,
) -> int:
    """在给定矩形里能放下 `text` 的前多少个字符(尽量在段落/句号处切开)。

    `columns=True` 时按竖栏计量:同样高度能装下更多字,这才是报纸栏而不是博客通栏。
    `col_rects` 给出各栏实高时,总行数按栏加总(图旁那几栏更高)。
    """
    if not text:
        return 0
    if col_rects is not None:
        usable = [c for c in col_rects if c.w > 1 and c.h > 1]
        if not usable:
            return 0
        col_w = usable[0].w
        lh = line_height_mm(font_size_pt, line_ratio)
        budget_lines = sum(max(0, int(c.h / lh)) for c in usable)
    else:
        if height_mm <= 1.0 or width_mm <= 1.0:
            return 0
        n_cols = column_count(width_mm) if columns else 1
        cols = column_rects(MmRect(0, 0, width_mm, height_mm), n_cols)
        col_w = cols[0].w
        lines_per = max(0, int(height_mm / line_height_mm(font_size_pt, line_ratio)))
        budget_lines = lines_per * max(1, len(cols))
    if budget_lines <= 0:
        return 0
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        n = len(wrap_text(text[:mid], col_w, font_size_pt))
        if n <= budget_lines:
            lo = mid
        else:
            hi = mid - 1
    return _snap_break(text, lo) if snap else lo


def _snap_break(text: str, idx: int) -> int:
    """尽量在句号/段末切开,避免续文以「级水平。」这种残句开头。"""
    if idx >= len(text):
        return len(text)
    if idx <= 0:
        return 0
    window = text[max(0, idx - 96) : idx]
    for sep in ("\n\n", "。", "！", "？", "；"):
        p = window.rfind(sep)
        if p >= 0:
            return idx - (len(window) - p - len(sep))
    for sep in ("\n", "，", "、", ".", "!", "?", ";", ",", " "):
        p = window.rfind(sep)
        if p >= 0:
            return idx - (len(window) - p - len(sep))
    return idx


def split_body(text: str, n_chars: int) -> tuple[str, str]:
    if n_chars <= 0:
        return "", text
    if n_chars >= len(text):
        return text, ""
    n_chars = _snap_break(text, n_chars)
    head, tail = text[:n_chars].rstrip(), text[n_chars:].lstrip()
    return head, tail


@dataclass(frozen=True)
class TypeSpec:
    kind: str
    body_pt: float
    line_ratio: float
    title_lead_pt: float
    title_pt: float
    kicker_pt: float
    lede_pt: float
    pad_mm: float = 1.4
    rule_mm: float = 0.18
    kicker_bar_mm: float = 3.2

    @classmethod
    def for_kind(cls, kind: str) -> TypeSpec:
        if kind == "pm":
            return cls(
                kind="pm",
                body_pt=8.7,
                line_ratio=1.38,
                title_lead_pt=34.0,
                title_pt=20.0,
                kicker_pt=6.8,
                lede_pt=9.0,
            )
        return cls(
            kind="am",
            body_pt=8.15,
            line_ratio=1.30,
            title_lead_pt=32.0,
            title_pt=19.0,
            kicker_pt=6.6,
            lede_pt=8.6,
        )


def title_size(section: str, part: int, types: TypeSpec, *, lead: bool = False) -> float:
    if part > 0:
        # 跳页题:比正文大、比首发题小。读者翻到这版,靠题面认文章,
        # 「上接第N版」只是 kicker 里的辅助信息。
        return max(11.5, types.title_pt - 6.5)
    if lead and section == "headline":
        return types.title_lead_pt
    if section == "headline":
        return types.title_pt
    if section in ("deepread", "critical"):
        return types.title_pt - 1.2
    return max(11.5, types.title_pt - 2.5)


def estimate_title_height_mm(title: str, width_mm: float, size_pt: float, types: TypeSpec) -> float:
    n = title_wrap_line_count(title, width_mm, size_pt)
    h = n * line_height_mm(size_pt, 1.12)
    return h + types.kicker_bar_mm + TITLE_SLACK_MM


def cells_for_height(height_mm: float, geom: PageGeom) -> int:
    step = geom.cell_h + geom.gutter
    if step <= 0:
        return 3
    return max(3, int((height_mm + geom.gutter * 0.4) / step + 0.999))
