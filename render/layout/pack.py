"""天际线装箱:稿件是矩形,从上往下贴轮廓,不允许 L 形。

hole-first 用 maximal free rects 对着空矩形派稿,比 MaxRects BSSF
少切出 1 栏宽的竖缝。n 很小(4×8),空矩形可以扫格子穷举。
"""
from __future__ import annotations

from render.layout.grid import CellRect


class Skyline:
    """每列记录「第一空行」。放置贴着轮廓最低处、尽量靠左。"""

    def __init__(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        self.used: list[CellRect] = []
        self.sky: list[int] = [0] * cols

    def occupy(self, c: int, r: int, w: int, h: int) -> CellRect:
        placed = CellRect(c, r, w, h)
        if c < 0 or r < 0 or c + w > self.cols or r + h > self.rows:
            raise ValueError(f"out of grid: {placed} in {self.cols}x{self.rows}")
        if any(placed.overlaps(u) for u in self.used):
            raise ValueError(f"overlap: {placed} vs {self.used}")
        self.used.append(placed)
        self._recompute_sky()
        return placed

    def _recompute_sky(self) -> None:
        self.sky = [0] * self.cols
        for u in self.used:
            for x in range(u.c, u.c + u.w):
                self.sky[x] = max(self.sky[x], u.r + u.h)

    def can_place(self, w: int, h: int) -> bool:
        return self.find(w, h) is not None

    def find(self, w: int, h: int) -> CellRect | None:
        best: tuple[int, int, int] | None = None
        found: CellRect | None = None
        for c in range(self.cols - w + 1):
            y = max(self.sky[c : c + w])
            if y + h > self.rows:
                continue
            waste = sum(y - self.sky[x] for x in range(c, c + w))
            key = (y, waste, c)
            if best is None or key < best:
                best = key
                found = CellRect(c, y, w, h)
        return found

    def place(self, w: int, h: int) -> CellRect | None:
        found = self.find(w, h)
        if found is None:
            return None
        return self.occupy(found.c, found.r, found.w, found.h)

    def region_free(self, c: int, r: int, w: int, h: int) -> bool:
        if w < 1 or h < 1:
            return False
        if c < 0 or r < 0 or c + w > self.cols or r + h > self.rows:
            return False
        rect = CellRect(c, r, w, h)
        return all(not rect.overlaps(u) for u in self.used)

    def remaining_area(self) -> int:
        return sum(fr.area for fr in self.free_rects())

    def free_rects(self) -> list[CellRect]:
        return maximal_free_rects(self.cols, self.rows, self.used)


def maximal_free_rects(
    cols: int, rows: int, used: list[CellRect]
) -> list[CellRect]:
    """被占用格子之外的极大空矩形,面积大的可以先派稿。"""
    occ = [[False] * cols for _ in range(rows)]
    for u in used:
        for r in range(u.r, min(rows, u.r + u.h)):
            for c in range(u.c, min(cols, u.c + u.w)):
                occ[r][c] = True
    cands: list[CellRect] = []
    for r0 in range(rows):
        for c0 in range(cols):
            if occ[r0][c0]:
                continue
            max_w = 0
            while c0 + max_w < cols and not occ[r0][c0 + max_w]:
                max_w += 1
            w = max_w
            for r1 in range(r0, rows):
                row_w = 0
                while (
                    row_w < w
                    and c0 + row_w < cols
                    and not occ[r1][c0 + row_w]
                ):
                    row_w += 1
                if row_w == 0:
                    break
                w = min(w, row_w)
                cands.append(CellRect(c0, r0, w, r1 - r0 + 1))
    return _prune(cands)


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


# 旧测试名:天际线同样提供 place / occupy。
MaxRects = Skyline
