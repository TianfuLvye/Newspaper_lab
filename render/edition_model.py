"""期次解析用的文章模型。只描述「印什么」,不描述格子怎么摆。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageSpec:
    """一篇稿最多吃 1–3 张图。没有真实文件时也可以只带宽高。"""

    src: str = ""
    width_px: int = 1200
    height_px: int = 800
    caption: str = ""
    alt: str = ""

    @property
    def aspect(self) -> float:
        return max(self.width_px, 1) / max(self.height_px, 1)


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
    source: str = ""
