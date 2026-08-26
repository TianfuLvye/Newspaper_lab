"""A3 矩阵报纸排版(Lab 8)。"""

from render.layout.engine import layout_edition
from render.layout.grid import PageGeom
from render.layout.model import Article, ImageSpec, LayoutResult

__all__ = [
    "Article",
    "ImageSpec",
    "LayoutResult",
    "PageGeom",
    "layout_edition",
]
