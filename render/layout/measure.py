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


def chars_that_fit(
    text: str,
    width_mm: float,
    height_mm: float,
    font_size_pt: float,
    line_ratio: float = 1.36,
    *,
    columns: bool = True,
) -> int:
    """在给定矩形里能放下 `text` 的前多少个字符(尽量在段落/句号处切开)。

    `columns=True` 时按竖栏计量:同样高度能装下更多字,这才是报纸栏而不是博客通栏。
    """
    if height_mm <= 1.0 or width_mm <= 1.0 or not text:
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
    return _snap_break(text, lo)


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
    h = text_height_mm(title, width_mm, size_pt, line_ratio=1.12)
    return h + types.kicker_bar_mm + 2.2


def cells_for_height(height_mm: float, geom: PageGeom) -> int:
    step = geom.cell_h + geom.gutter
    if step <= 0:
        return 3
    return max(3, int((height_mm + geom.gutter * 0.4) / step + 0.999))
