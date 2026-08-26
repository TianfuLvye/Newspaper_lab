"""整数网格上的 MaxRects(Best Short Side Fit)。

每篇文章占一块矩形,不允许 L 形、不允许重叠。这是报纸排版和 CSS 多栏流
的本质差别:先切矩形,再往格子里倒字。
"""
from __future__ import annotations

from render.layout.grid import CellRect


class MaxRects:
    def __init__(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        self.free: list[CellRect] = [CellRect(0, 0, cols, rows)]
        self.used: list[CellRect] = []

    def occupy(self, c: int, r: int, w: int, h: int) -> CellRect:
        placed = CellRect(c, r, w, h)
        if c < 0 or r < 0 or c + w > self.cols or r + h > self.rows:
            raise ValueError(f"out of grid: {placed} in {self.cols}x{self.rows}")
        self.used.append(placed)
        self._split(placed)
        return placed

    def can_place(self, w: int, h: int) -> bool:
        return self.find(w, h) is not None

    def find(self, w: int, h: int) -> CellRect | None:
        best: tuple[int, int, int, int] | None = None
        for fr in self.free:
            if fr.w >= w and fr.h >= h:
                sx = fr.w - w
                sy = fr.h - h
                key = (min(sx, sy), max(sx, sy), fr.r, fr.c)
                if best is None or key < best:
                    best = key
        if best is None:
            return None
        _, _, r, c = best
        return CellRect(c, r, w, h)

    def place(self, w: int, h: int) -> CellRect | None:
        found = self.find(w, h)
        if found is None:
            return None
        return self.occupy(found.c, found.r, found.w, found.h)

    def remaining_area(self) -> int:
        return sum(fr.area for fr in self.free)

    def _split(self, placed: CellRect) -> None:
        nxt: list[CellRect] = []
        for fr in self.free:
            if not fr.overlaps(placed):
                nxt.append(fr)
                continue
            if fr.c < placed.c:
                nxt.append(CellRect(fr.c, fr.r, placed.c - fr.c, fr.h))
            right = placed.c + placed.w
            if fr.c + fr.w > right:
                nxt.append(CellRect(right, fr.r, fr.c + fr.w - right, fr.h))
            if fr.r < placed.r:
                nxt.append(CellRect(fr.c, fr.r, fr.w, placed.r - fr.r))
            bottom = placed.r + placed.h
            if fr.r + fr.h > bottom:
                nxt.append(CellRect(fr.c, bottom, fr.w, fr.r + fr.h - bottom))
        self.free = _prune(nxt)


def _prune(rects: list[CellRect]) -> list[CellRect]:
    cleaned = [r for r in rects if r.w > 0 and r.h > 0]
    keep: list[CellRect] = []
    for i, a in enumerate(cleaned):
        drop = False
        for j, b in enumerate(cleaned):
            if i == j:
                continue
            if b.contains(a) and (b.area > a.area or j < i):
                drop = True
                break
        if not drop:
            keep.append(a)
    return keep


def no_overlaps(rects: list[CellRect]) -> bool:
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            if a.overlaps(b):
                return False
    return True
