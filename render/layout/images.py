"""一篇稿带 1–3 张图时,如何从文章矩形里切出「图井」,剩下一块矩形给正文。

项目现在还没有稳定的配图管道,但排版不能等图来了再发明规则。本模块是
预留算法:输入 ImageSpec(可以没有文件,只带宽高),输出图框 + 单一矩形
正文区。正文区故意保持矩形——不绕排、不 L 形——这样分页和量字仍然简单。

决策摘要
--------
1. 图井是文章内容区(去掉标题带)内部的一块矩形,最多吃掉 45% 面积。
2. 1 张:横图靠上,竖图靠左,方图看文章是横的还是竖的。
3. 2 张:优先顶栏并排;两张都竖则改左栏叠放。
4. 3 张:顶栏 2/3 英雄图 + 右侧两张小图(杂志常见);宽度不够则三连。
5. 缩放:先按 contain 放进格;短边小于 MIN_PHOTO_MM 再改 cover 裁切;
   还不够就把多出来的图丢给续页(overflow_images)。
6. 续页默认不再重复已用过的图,除非第一截连图井都放不下。

以后真有图文件时,只要把 path 填进 ImageSpec.src,PDF/HTML 渲染器会读它;
算法本身不依赖磁盘。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from render.layout.grid import MmRect
from render.layout.model import ImageBox, ImageSpec

MIN_PHOTO_MM = 28.0
MAX_WELL_FRACTION = 0.45
CAPTION_MM = 4.0
GUTTER_MM = 2.0
MIN_TEXT_MM = 22.0  # 图再抢也不能让正文只剩一行半


def classify(img: ImageSpec) -> str:
    a = img.aspect
    if a >= 1.25:
        return "land"
    if a <= 0.80:
        return "port"
    return "sq"


@dataclass
class ImagePlan:
    """对某一篇文章矩形的配图方案。"""

    variant: str
    image_boxes: list[ImageBox] = field(default_factory=list)
    text_rect: MmRect | None = None
    overflow_images: list[ImageSpec] = field(default_factory=list)
    well: MmRect | None = None
    note: str = ""


def plan_image_slots(
    content: MmRect,
    images: list[ImageSpec],
    *,
    min_photo_mm: float = MIN_PHOTO_MM,
    max_well_fraction: float = MAX_WELL_FRACTION,
) -> ImagePlan:
    """在 content 内切图井。images 超过 3 张时只排前三,其余进 overflow。"""
    if content.w <= 1 or content.h <= 1:
        return ImagePlan("none", text_rect=content, overflow_images=list(images),
                         note="content too small")

    pending = list(images[:3])
    extra = list(images[3:])
    n = len(pending)
    if n == 0:
        return ImagePlan("none", text_rect=content, overflow_images=extra)

    well, text, variant = _choose_well(content, pending)
    if well is None or text is None:
        return ImagePlan(
            "overflow-all",
            text_rect=content,
            overflow_images=pending + extra,
            note="no well without starving text",
        )

    boxes, leftover = _fill_well(well, pending, variant, min_photo_mm=min_photo_mm)
    # 面积约束:图井不得超 max_well_fraction。超了就按比例压矮/压窄。
    well_area = well.w * well.h
    room = content.w * content.h
    if room > 0 and well_area > max_well_fraction * room + 1e-6:
        well, text, boxes = _shrink_well(
            content, well, variant, pending, min_photo_mm, max_well_fraction
        )
        leftover = leftover  # 缩井可能导致更多 overflow,下面重填
        boxes, leftover = _fill_well(well, pending, variant, min_photo_mm=min_photo_mm)

    return ImagePlan(
        variant=variant,
        image_boxes=boxes,
        text_rect=text,
        overflow_images=leftover + extra,
        well=well,
        note=f"n={n} variant={variant}",
    )


def estimate_well_height_mm(images: list[ImageSpec], width_mm: float) -> float:
    """装箱前的粗估:假定文章大约 3 栏宽,图井高度。"""
    n = min(len(images), 3)
    if n == 0 or width_mm <= 0:
        return 0.0
    kinds = [classify(img) for img in images[:n]]
    if n == 1 and kinds[0] == "port":
        return 0.0  # 竖图走左栏,主要吃宽度
    if n == 1:
        return min(width_mm / max(images[0].aspect, 0.4), 52.0) + CAPTION_MM
    if n == 2 and all(k == "port" for k in kinds):
        return 0.0
    if n == 3:
        return min(58.0, width_mm * 0.42)
    return min(48.0, width_mm * 0.36)


def estimate_well_width_mm(images: list[ImageSpec], height_mm: float) -> float:
    n = min(len(images), 3)
    if n == 0:
        return 0.0
    kinds = [classify(img) for img in images[:n]]
    if n == 1 and kinds[0] == "port":
        return min(height_mm * images[0].aspect, 48.0)
    if n == 2 and all(k == "port" for k in kinds):
        return min(42.0, height_mm * 0.38)
    return 0.0


def _choose_well(content: MmRect, images: list[ImageSpec]) -> tuple[MmRect | None, MmRect | None, str]:
    n = len(images)
    kinds = [classify(img) for img in images]
    # 正文至少留下 MIN_TEXT_MM 的一条边
    if n == 1:
        k = kinds[0]
        if k == "land" or (k == "sq" and content.w <= content.h * 1.15):
            h = min(content.h * 0.40, content.w / max(images[0].aspect, 0.3) + CAPTION_MM)
            h = max(min_photo_cap(h), min(h, content.h - MIN_TEXT_MM))
            if h < MIN_PHOTO_MM or content.h - h < MIN_TEXT_MM:
                return None, None, "fail"
            well, text = content.split_top(h)
            return well, text, "top-1"
        w = min(content.w * 0.42, content.h * images[0].aspect + 1.0)
        w = max(MIN_PHOTO_MM, min(w, content.w - MIN_TEXT_MM))
        if content.w - w < MIN_TEXT_MM:
            return None, None, "fail"
        well, text = content.split_left(w)
        return well, text, "left-1"

    if n == 2:
        if all(k == "port" for k in kinds) and content.h >= content.w:
            w = min(content.w * 0.40, 46.0)
            if content.w - w < MIN_TEXT_MM:
                return None, None, "fail"
            well, text = content.split_left(w)
            return well, text, "left-stack-2"
        h = min(content.h * 0.38, 50.0)
        if content.h - h < MIN_TEXT_MM:
            return None, None, "fail"
        well, text = content.split_top(h)
        return well, text, "top-2"

    # n == 3
    if content.w >= 90:
        h = min(content.h * 0.42, 58.0)
        if content.h - h < MIN_TEXT_MM:
            return None, None, "fail"
        well, text = content.split_top(h)
        return well, text, "hero-plus-2"
    h = min(content.h * 0.32, 42.0)
    if content.h - h < MIN_TEXT_MM:
        return None, None, "fail"
    well, text = content.split_top(h)
    return well, text, "top-3"


def min_photo_cap(h: float) -> float:
    return max(h, MIN_PHOTO_MM + CAPTION_MM)


def _shrink_well(
    content: MmRect,
    well: MmRect,
    variant: str,
    images: list[ImageSpec],
    min_photo_mm: float,
    max_frac: float,
) -> tuple[MmRect, MmRect, list[ImageBox]]:
    room = content.w * content.h
    target = max_frac * room
    if well.w * well.h <= target or well.w * well.h <= 1:
        boxes, _ = _fill_well(well, images, variant, min_photo_mm=min_photo_mm)
        text = _text_complement(content, well, variant)
        return well, text, boxes
    scale = (target / (well.w * well.h)) ** 0.5
    if variant.startswith("left"):
        w = max(min_photo_mm, well.w * scale)
        well, text = content.split_left(w)
    else:
        h = max(min_photo_mm + CAPTION_MM, well.h * scale)
        well, text = content.split_top(h)
    boxes, _ = _fill_well(well, images, variant, min_photo_mm=min_photo_mm)
    return well, text, boxes


def _text_complement(content: MmRect, well: MmRect, variant: str) -> MmRect:
    if variant.startswith("left"):
        return MmRect(well.right, content.y, max(0.0, content.right - well.right), content.h)
    return MmRect(content.x, well.bottom, content.w, max(0.0, content.bottom - well.bottom))


def _fill_well(
    well: MmRect,
    images: list[ImageSpec],
    variant: str,
    *,
    min_photo_mm: float,
) -> tuple[list[ImageBox], list[ImageSpec]]:
    if variant == "top-1" or variant == "left-1":
        box = _fit_one(well, images[0], min_photo_mm)
        leftover = images[1:]
        if box.overflow:
            leftover = images[:]
            return [], leftover
        return [box], leftover

    if variant == "top-2":
        boxes, overflow = _split_strip(
            well, images[:2], axis="x", min_photo_mm=min_photo_mm
        )
        return boxes, overflow + images[2:]

    if variant == "left-stack-2":
        boxes, overflow = _split_strip(
            well, images[:2], axis="y", min_photo_mm=min_photo_mm
        )
        return boxes, overflow + images[2:]

    if variant == "top-3":
        boxes, overflow = _split_strip(
            well, images[:3], axis="x", min_photo_mm=min_photo_mm
        )
        return boxes, overflow

    if variant == "hero-plus-2":
        hero_w = well.w * 0.62
        hero_rect = MmRect(well.x, well.y, hero_w, well.h)
        side = MmRect(well.x + hero_w + GUTTER_MM, well.y,
                      max(0.0, well.w - hero_w - GUTTER_MM), well.h)
        hero = _fit_one(hero_rect, images[0], min_photo_mm)
        others, overflow = _split_strip(side, images[1:3], axis="y", min_photo_mm=min_photo_mm)
        boxes = []
        leftover: list[ImageSpec] = []
        if hero.overflow:
            leftover.append(images[0])
        else:
            boxes.append(hero)
        boxes.extend(others)
        leftover.extend(overflow)
        return boxes, leftover

    # 未知 variant:全塞 overflow
    return [], list(images)


def _split_strip(
    well: MmRect,
    images: list[ImageSpec],
    *,
    axis: str,
    min_photo_mm: float,
) -> tuple[list[ImageBox], list[ImageSpec]]:
    n = len(images)
    if n == 0 or well.w <= 0 or well.h <= 0:
        return [], list(images)
    boxes: list[ImageBox] = []
    leftover: list[ImageSpec] = []
    if axis == "x":
        slot_w = (well.w - GUTTER_MM * (n - 1)) / n
        if slot_w < min_photo_mm * 0.85:
            # 并排太窄,只留第一张,其余 overflow
            boxes.append(_fit_one(well, images[0], min_photo_mm))
            return boxes, images[1:]
        for i, img in enumerate(images):
            rect = MmRect(well.x + i * (slot_w + GUTTER_MM), well.y, slot_w, well.h)
            box = _fit_one(rect, img, min_photo_mm)
            if box.overflow:
                leftover.append(img)
            else:
                boxes.append(box)
        return boxes, leftover
    slot_h = (well.h - GUTTER_MM * (n - 1)) / n
    if slot_h < min_photo_mm * 0.75:
        boxes.append(_fit_one(well, images[0], min_photo_mm))
        return boxes, images[1:]
    for i, img in enumerate(images):
        rect = MmRect(well.x, well.y + i * (slot_h + GUTTER_MM), well.w, slot_h)
        box = _fit_one(rect, img, min_photo_mm)
        if box.overflow:
            leftover.append(img)
        else:
            boxes.append(box)
    return boxes, leftover


def _fit_one(slot: MmRect, img: ImageSpec, min_photo_mm: float) -> ImageBox:
    """contain 进格子;短边过小则 cover;再小就 overflow。caption 从格子底部扣。"""
    photo = slot.inset(b=CAPTION_MM if img.caption or True else 0)
    if photo.w < 8 or photo.h < 8:
        return ImageBox(img, slot, fitted="placeholder", scale=0.0, overflow=True)
    nat_w, nat_h = img.natural_mm()
    if nat_w <= 0 or nat_h <= 0:
        nat_w, nat_h = photo.w, photo.h
    contain = min(photo.w / nat_w, photo.h / nat_h)
    fitted_w, fitted_h = nat_w * contain, nat_h * contain
    mode = "contain"
    if min(fitted_w, fitted_h) < min_photo_mm:
        # 改 cover:填满格子,允许裁切
        cover = max(photo.w / nat_w, photo.h / nat_h)
        fitted_w, fitted_h = photo.w, photo.h
        contain = cover
        mode = "cover"
        if min(photo.w, photo.h) < min_photo_mm * 0.72:
            return ImageBox(img, slot, fitted="placeholder", scale=contain, overflow=True)
    # 居中放置(contain 时图可能小于格子)
    x = photo.x + max(0.0, (photo.w - min(fitted_w, photo.w)) / 2)
    y = photo.y + max(0.0, (photo.h - min(fitted_h, photo.h)) / 2)
    rect = MmRect(x, y, min(fitted_w, photo.w), min(fitted_h, photo.h))
    if not img.src:
        mode = "placeholder"
    return ImageBox(img, rect, fitted=mode, scale=contain, overflow=False)
