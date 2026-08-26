"""A3 纸面几何:固定大小的纸,切成整数网格。

坐标原点在页面左上角,单位毫米。装箱在格子坐标里做,画的时候再换成 mm。
页脚在网格之外,避免页码和文章抢格子。
"""
from __future__ import annotations

from dataclasses import dataclass

A3_W_MM = 297.0
A3_H_MM = 420.0
PT_PER_MM = 72.0 / 25.4
MM_PER_PT = 25.4 / 72.0


def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_MM


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_PT


@dataclass(frozen=True)
class CellRect:
    """网格上的闭区间矩形:[c, c+w) × [r, r+h)。"""

    c: int
    r: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    def overlaps(self, other: CellRect) -> bool:
        return not (
            self.c + self.w <= other.c
            or other.c + other.w <= self.c
            or self.r + self.h <= other.r
            or other.r + other.h <= self.r
        )

    def contains(self, other: CellRect) -> bool:
        return (
            self.c <= other.c
            and self.r <= other.r
            and self.c + self.w >= other.c + other.w
            and self.r + self.h >= other.r + other.h
        )


@dataclass(frozen=True)
class MmRect:
    x: float
    y: float  # 距页顶
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def inset(self, t: float = 0, r: float = 0, b: float = 0, l: float = 0) -> MmRect:
        return MmRect(self.x + l, self.y + t, max(0.0, self.w - l - r), max(0.0, self.h - t - b))

    def split_top(self, height: float) -> tuple[MmRect, MmRect]:
        h = min(max(height, 0.0), self.h)
        return MmRect(self.x, self.y, self.w, h), MmRect(self.x, self.y + h, self.w, self.h - h)

    def split_left(self, width: float) -> tuple[MmRect, MmRect]:
        w = min(max(width, 0.0), self.w)
        return MmRect(self.x, self.y, w, self.h), MmRect(self.x + w, self.y, self.w - w, self.h)


@dataclass(frozen=True)
class PageGeom:
    """一页可印区域 → 固定行列的矩阵。早报晚报共用同一张 A3,只改字号不改纸。"""

    page_w: float = A3_W_MM
    page_h: float = A3_H_MM
    margin_l: float = 9.0
    margin_r: float = 9.0
    margin_t: float = 8.0
    margin_b: float = 8.0
    footer_h: float = 7.0
    gutter: float = 2.6
    cols: int = 6
    rows: int = 14

    @property
    def content_x(self) -> float:
        return self.margin_l

    @property
    def content_y(self) -> float:
        return self.margin_t

    @property
    def content_w(self) -> float:
        return self.page_w - self.margin_l - self.margin_r

    @property
    def content_h(self) -> float:
        return self.page_h - self.margin_t - self.margin_b - self.footer_h

    @property
    def cell_w(self) -> float:
        return (self.content_w - (self.cols - 1) * self.gutter) / self.cols

    @property
    def cell_h(self) -> float:
        return (self.content_h - (self.rows - 1) * self.gutter) / self.rows

    def cell_to_mm(self, rect: CellRect) -> MmRect:
        x = self.content_x + rect.c * (self.cell_w + self.gutter)
        y = self.content_y + rect.r * (self.cell_h + self.gutter)
        w = rect.w * self.cell_w + max(rect.w - 1, 0) * self.gutter
        h = rect.h * self.cell_h + max(rect.h - 1, 0) * self.gutter
        return MmRect(x, y, w, h)

    def footer_rect(self) -> MmRect:
        y = self.page_h - self.margin_b - self.footer_h
        return MmRect(self.margin_l, y, self.content_w, self.footer_h)

    def usable_rows(self, page_index: int) -> int:
        reserved = 2 if page_index == 0 else 1
        return self.rows - reserved
