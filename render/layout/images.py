"""一篇稿带 1–3 张图时,如何从文章矩形里切出「图井」。

图井按正文栏格咬合宽度:横图居中占整数栏,竖图靠左占整数栏。正文区仍是
整块内容矩形,量字时用 punch_columns 按栏抠掉图占的高度——邻栏从头顶排
字,图所在栏从图下接字。稿件外框仍然是矩形,不做 Word 那种行内折行。

决策摘要
--------
1. 图井最多吃掉 45% 面积;井高等于图高 + 说明,顶对齐,不在井里垂直居中。
2. 1 张:横图靠上咬栏,竖图靠左咬栏,方图看文章是横的还是竖的。
3. 2 张:优先顶栏并排铺满;两张都竖则改左栏叠放。
4. 3 张:顶栏 2/3 英雄图 + 右侧两张小图;宽度不够则三连。
5. 缩放:先按 contain 放进格;短边小于 MIN_PHOTO_MM 再改 cover 裁切;
   还不够就把多出来的图丢给续页(overflow_images)。
6. 图铺满全部栏宽时,所有栏都从图下开始,退化为原来的上下型。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from render.layout.grid import MmRect
from render.layout.measure import column_rects
from render.layout.model import ImageBox, ImageSpec

MIN_PHOTO_MM = 28.0
MAX_WELL_FRACTION = 0.45
CAPTION_MM = 4.0
GUTTER_MM = 2.0
MIN_TEXT_MM = 22.0  # 图再抢也不能让正文只剩一行半
LETTERBOX_SKIP = 0.82  # 井比图宽太多就改占更少栏,避免图1那种两侧空井


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


def wrap_obstacles(well: MmRect | None, boxes: list[ImageBox]) -> list[MmRect]:
    """绕排障碍:优先用咬过栏的图井,否则用图框加说明高。"""
    if well is not None and well.w > 1 and well.h > 1:
        return [well]
    out: list[MmRect] = []
    for ib in boxes:
        r = ib.rect
        out.append(MmRect(r.x, r.y, r.w, r.h + CAPTION_MM))
    return out


def plan_image_slots(
    content: MmRect,
    images: list[ImageSpec],
    *,
    min_photo_mm: float = MIN_PHOTO_MM,
    max_well_fraction: float = MAX_WELL_FRACTION,
) -> ImagePlan:
    """在 content 内切图井。images 超过 3 张时只排前三,其余进 overflow。

    `text_rect` 永远是整块 content:字从哪一栏、哪一高度起排,由 punch_columns 决定。
    """
    if content.w <= 1 or content.h <= 1:
        return ImagePlan("none", text_rect=content, overflow_images=list(images),
                         note="content too small")

    pending = list(images[:3])
    extra = list(images[3:])
    n = len(pending)
    if n == 0:
        return ImagePlan("none", text_rect=content, overflow_images=extra)

    well, variant = _choose_well(content, pending)
    if well is None:
        return ImagePlan(
            "overflow-all",
            text_rect=content,
            overflow_images=pending + extra,
            note="no well without starving text",
        )

    boxes, leftover = _fill_well(well, pending, variant, min_photo_mm=min_photo_mm)
    well_area = well.w * well.h
    room = content.w * content.h
    if room > 0 and well_area > max_well_fraction * room + 1e-6:
        well, boxes = _shrink_well(
            content, well, variant, pending, min_photo_mm, max_well_fraction
        )
        boxes, leftover = _fill_well(well, pending, variant, min_photo_mm=min_photo_mm)

    return ImagePlan(
        variant=variant,
        image_boxes=boxes,
        text_rect=content,
        overflow_images=leftover + extra,
        well=well,
        note=f"n={n} variant={variant}",
    )


def estimate_well_height_mm(images: list[ImageSpec], width_mm: float) -> float:
    """装箱前的粗估:横图按咬栏后的高度,竖图走左栏不占高度。"""
    n = min(len(images), 3)
    if n == 0 or width_mm <= 0:
        return 0.0
    kinds = [classify(img) for img in images[:n]]
    if n == 1 and kinds[0] == "port":
        return 0.0
    if n == 1:
        aspect = max(images[0].aspect, 0.4)
        # 宽稿通常咬 2–3 栏,约 0.6 倍通栏宽,图比通栏 contain 更高一点。
        span_w = min(width_mm, max(width_mm * 0.62, 52.0 * aspect))
        return min(span_w / aspect, 58.0) + CAPTION_MM
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


def _choose_well(content: MmRect, images: list[ImageSpec]) -> tuple[MmRect | None, str]:
    n = len(images)
    kinds = [classify(img) for img in images]
    if n == 1:
        k = kinds[0]
        if k == "land" or (k == "sq" and content.w <= content.h * 1.15):
            well = _top_well_for_aspect(content, images[0].aspect, h_frac=0.40)
            return well, "top-1"
        well = _left_well_for_aspect(content, images[0].aspect)
        return well, "left-1"

    if n == 2:
        if all(k == "port" for k in kinds) and content.h >= content.w:
            well = _left_stack_well(content)
            return well, "left-stack-2"
        h = min(content.h * 0.38, 50.0)
        well = _top_strip(content, h)
        return well, "top-2"

    if content.w >= 90:
        h = min(content.h * 0.42, 58.0)
        well = _top_strip(content, h)
        return well, "hero-plus-2"
    h = min(content.h * 0.32, 42.0)
    well = _top_strip(content, h)
    return well, "top-3"


def min_photo_cap(h: float) -> float:
    return max(h, MIN_PHOTO_MM + CAPTION_MM)


def _top_strip(content: MmRect, height: float) -> MmRect | None:
    """铺满全部栏宽的顶栏,两张并排 / 英雄图走这条,视觉上仍是上下型。"""
    h = min(height, content.h - MIN_TEXT_MM)
    if h < MIN_PHOTO_MM or content.h - h < MIN_TEXT_MM:
        return None
    return MmRect(content.x, content.y, content.w, h)


def _top_well_for_aspect(content: MmRect, aspect: float, *, h_frac: float) -> MmRect | None:
    """按宽高比把顶图咬到整数栏,居中放置。"""
    cols = column_rects(content)
    n = len(cols)
    h_max = min(content.h * h_frac, content.h - MIN_TEXT_MM)
    if h_max < MIN_PHOTO_MM or n < 1:
        return None
    room = content.w * content.h
    best: MmRect | None = None
    best_unused = 1e9
    aspect = max(aspect, 0.15)
    for span in range(1, n + 1):
        start = (n - span) // 2
        w = cols[start + span - 1].right - cols[start].x
        h = w / aspect + CAPTION_MM
        if h > h_max + 0.4:
            h = h_max
            nat_w = max(h - CAPTION_MM, 1.0) * aspect
            if span > 1 and nat_w < w * LETTERBOX_SKIP:
                continue
        if h < MIN_PHOTO_MM:
            continue
        if content.h - h < MIN_TEXT_MM:
            continue
        if room > 0 and w * h > MAX_WELL_FRACTION * room + 1e-6:
            h = MAX_WELL_FRACTION * room / w
            if h < MIN_PHOTO_MM or content.h - h < MIN_TEXT_MM:
                continue
            nat_w = max(h - CAPTION_MM, 1.0) * aspect
            if span > 1 and nat_w < w * LETTERBOX_SKIP:
                continue
        unused = max(0.0, w - max(h - CAPTION_MM, 1.0) * aspect)
        narrower_tie = best is not None and abs(unused - best_unused) <= 0.3 and w < best.w
        if unused < best_unused - 0.3 or narrower_tie:
            best_unused = unused
            best = MmRect(cols[start].x, content.y, w, h)
    return best


def _left_well_for_aspect(content: MmRect, aspect: float) -> MmRect | None:
    """竖图靠左咬 1–2 栏,井高随宽高比,该栏图下还可以走字。"""
    cols = column_rects(content)
    n = len(cols)
    if n < 1:
        return None
    max_span = max(1, n - 1) if n > 1 else 1
    want_w = min(content.w * 0.42, content.h * max(aspect, 0.15) + 1.0)
    span = 1
    if n > 1:
        pitch = cols[1].x - cols[0].x
        if pitch > 1:
            span = max(1, min(max_span, int(round(want_w / pitch))))
    w = cols[span - 1].right - cols[0].x
    h = min(content.h, w / max(aspect, 0.15) + CAPTION_MM)
    if span == n:
        h = min(h, content.h - MIN_TEXT_MM)
    if h < MIN_PHOTO_MM:
        return None
    room = content.w * content.h
    if room > 0 and w * h > MAX_WELL_FRACTION * room + 1e-6:
        h = MAX_WELL_FRACTION * room / w
        if h < MIN_PHOTO_MM:
            return None
    return MmRect(cols[0].x, content.y, w, h)


def _left_stack_well(content: MmRect) -> MmRect | None:
    cols = column_rects(content)
    n = len(cols)
    if n < 1:
        return None
    max_span = max(1, n - 1) if n > 1 else 1
    want_w = min(content.w * 0.40, 46.0)
    span = 1
    if n > 1:
        pitch = cols[1].x - cols[0].x
        if pitch > 1:
            span = max(1, min(max_span, int(round(want_w / pitch))))
    w = cols[span - 1].right - cols[0].x
    if n > 1 and content.w - w < MIN_TEXT_MM:
        return None
    h = content.h if n > 1 else min(content.h, content.h - MIN_TEXT_MM)
    if h < MIN_PHOTO_MM:
        return None
    return MmRect(cols[0].x, content.y, w, h)


def _shrink_well(
    content: MmRect,
    well: MmRect,
    variant: str,
    images: list[ImageSpec],
    min_photo_mm: float,
    max_frac: float,
) -> tuple[MmRect, list[ImageBox]]:
    room = content.w * content.h
    target = max_frac * room
    if well.w * well.h <= target or well.w * well.h <= 1:
        boxes, _ = _fill_well(well, images, variant, min_photo_mm=min_photo_mm)
        return well, boxes
    h = max(min_photo_mm + CAPTION_MM, target / well.w)
    well = MmRect(well.x, well.y, well.w, min(h, well.h))
    boxes, _ = _fill_well(well, images, variant, min_photo_mm=min_photo_mm)
    return well, boxes


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
    """contain 进格子;短边过小则 cover;再小就 overflow。caption 从格子底部扣。

    图顶对齐:井已经按栏咬过,再垂直居中会在图上方留出图1那种空带。
    """
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
        cover = max(photo.w / nat_w, photo.h / nat_h)
        fitted_w, fitted_h = photo.w, photo.h
        contain = cover
        mode = "cover"
        if min(photo.w, photo.h) < min_photo_mm * 0.72:
            return ImageBox(img, slot, fitted="placeholder", scale=contain, overflow=True)
    x = photo.x + max(0.0, (photo.w - min(fitted_w, photo.w)) / 2)
    y = photo.y
    rect = MmRect(x, y, min(fitted_w, photo.w), min(fitted_h, photo.h))
    if not img.src:
        mode = "placeholder"
    return ImageBox(img, rect, fitted=mode, scale=contain, overflow=False)
