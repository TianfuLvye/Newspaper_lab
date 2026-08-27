"""Lab 5:正文抽取。"""

from enrich.extract import (
    ExtractResult,
    extract,
    fill_item_content,
    quality_score,
    enrich_store,
)
from enrich.images import ImageMaterializer, harvest_page_images, pick_images

__all__ = [
    "ExtractResult",
    "extract",
    "fill_item_content",
    "quality_score",
    "enrich_store",
    "ImageMaterializer",
    "harvest_page_images",
    "pick_images",
]
