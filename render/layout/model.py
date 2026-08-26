"""排版用的文章 / 块 / 结果。和 Item 契约分开:这里只描述「印在纸上的一块」。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from render.layout.grid import CellRect, MmRect, PageGeom


@dataclass
class ImageSpec:
    """一篇稿最多吃 1–3 张图。没有真实文件时也可以只带宽高,算法照样跑。"""

    src: str = ""
    width_px: int = 1200
    height_px: int = 800
    caption: str = ""
    alt: str = ""

    @property
    def aspect(self) -> float:
        return max(self.width_px, 1) / max(self.height_px, 1)

    def natural_mm(self, dpi: float = 150.0) -> tuple[float, float]:
        return (self.width_px / dpi * 25.4, self.height_px / dpi * 25.4)


@dataclass
class Article:
    id: str
    section: str
    role: str  # story / index / placeholder
    title: str
    kicker: str = ""
    byline: str = ""
    body: str = ""
    url: str = ""
    images: list[ImageSpec] = field(default_factory=list)
    fn: str = ""
    priority: int = 100
    max_chars: int = 2800
    max_pages: int = 2
    empty: bool = False


@dataclass
class Chunk:
    """一篇稿切出来的一截。part=0 带标题和图,后续页只有正文。"""

    article: Article
    body: str
    images: list[ImageSpec]
    part: int = 0
    truncated: bool = False


@dataclass
class ImageBox:
    image: ImageSpec
    rect: MmRect
    fitted: str  # contain / cover / placeholder
    scale: float = 1.0
    overflow: bool = False


@dataclass
class PlacedBlock:
    chunk: Chunk | None
    cells: CellRect
    mm: MmRect
    page: int
    kind: str  # story / index / masthead / folio / placeholder
    title_rect: MmRect | None = None
    text_rect: MmRect | None = None
    image_boxes: list[ImageBox] = field(default_factory=list)
    jump_to: int | None = None
    jump_from: int | None = None
    section: str = ""
    article_id: str = ""
    n_text_cols: int = 1
    teasers: list[tuple[str, str, int]] = field(default_factory=list)


@dataclass
class PageLayout:
    index: int
    geom: PageGeom
    blocks: list[PlacedBlock] = field(default_factory=list)


@dataclass
class LayoutResult:
    kind: str
    edition_id: str
    lede: str
    geom: PageGeom
    pages: list[PageLayout]
    density: str
    warnings: list[str] = field(default_factory=list)
    n_articles: int = 0
    n_placeholders: int = 0

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    def to_json_obj(self) -> dict[str, Any]:
        pages = []
        for p in self.pages:
            blocks = []
            for b in p.blocks:
                blocks.append(
                    {
                        "kind": b.kind,
                        "section": b.section,
                        "article_id": b.article_id,
                        "page": b.page,
                        "cells": asdict(b.cells),
                        "mm": asdict(b.mm),
                        "part": None if b.chunk is None else b.chunk.part,
                        "title": "" if b.chunk is None else b.chunk.article.title,
                        "n_images": len(b.image_boxes),
                        "n_text_cols": b.n_text_cols,
                        "teasers": list(b.teasers),
                        "jump_to": b.jump_to,
                        "jump_from": b.jump_from,
                        "truncated": False if b.chunk is None else b.chunk.truncated,
                    }
                )
            pages.append({"index": p.index, "blocks": blocks})
        return {
            "kind": self.kind,
            "edition_id": self.edition_id,
            "lede": self.lede,
            "density": self.density,
            "n_pages": self.n_pages,
            "n_articles": self.n_articles,
            "n_placeholders": self.n_placeholders,
            "warnings": self.warnings,
            "paper": {"w_mm": self.geom.page_w, "h_mm": self.geom.page_h,
                      "cols": self.geom.cols, "rows": self.geom.rows},
            "pages": pages,
        }
