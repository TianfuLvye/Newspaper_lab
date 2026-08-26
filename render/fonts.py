"""找一张能印中文的字体。PDF 不依赖 WeasyPrint / Typst,只向系统要 TTF/TTC。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("fishnet.fonts")

# (path, subfontIndex, role)  role = body | title | any
_CANDIDATES: list[tuple[str, int, str]] = [
    # macOS 宋体:报纸正文该用衬线
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0, "body"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0, "title"),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0, "any"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0, "title"),
    ("/System/Library/Fonts/PingFang.ttc", 0, "any"),
    # Linux
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0, "body"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0, "title"),
    ("/usr/share/fonts/truetype/noto/NotoSerifSC-Regular.otf", 0, "body"),
    ("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf", 0, "title"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0, "any"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0, "any"),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0, "body"),
]


@dataclass
class FontBundle:
    body: str
    title: str
    body_path: str
    title_path: str
    source: str


_REGISTERED = False
_BUNDLE: FontBundle | None = None


def find_font_files() -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
    body = title = None
    for path, idx, role in _CANDIDATES:
        if not Path(path).exists():
            continue
        hit = (path, idx)
        if role in ("body", "any") and body is None:
            body = hit
        if role in ("title", "any") and title is None:
            title = hit
        if body and title:
            break
    if body and not title:
        title = body
    if title and not body:
        body = title
    return body, title


def register_pdf_fonts() -> FontBundle:
    """把中文字体注册进 reportlab。只做一次。"""
    global _REGISTERED, _BUNDLE
    if _REGISTERED and _BUNDLE is not None:
        return _BUNDLE

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    body, title = find_font_files()
    source = "ttf"
    if body is None:
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont

            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            pdfmetrics.registerFont(UnicodeCIDFont("STHeiti-Regular"))
            _BUNDLE = FontBundle(
                body="STSong-Light",
                title="STHeiti-Regular",
                body_path="",
                title_path="",
                source="cid",
            )
            _REGISTERED = True
            log.warning("未找到系统 CJK TTF,回退 CID STSong-Light(部分阅读器可能缺字)")
            return _BUNDLE
        except Exception as e:
            raise RuntimeError(
                "找不到中文字体。macOS 需要 Songti/STHeiti,Linux 需要 Noto CJK。"
            ) from e

    def _reg(name: str, path: str, idx: int) -> None:
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
        except TypeError:
            pdfmetrics.registerFont(TTFont(name, path))

    _reg("FishnetBody", body[0], body[1])
    if title[0] == body[0] and title[1] == body[1]:
        title_name = "FishnetBody"
    else:
        _reg("FishnetTitle", title[0], title[1])
        title_name = "FishnetTitle"
    _BUNDLE = FontBundle(
        body="FishnetBody",
        title=title_name,
        body_path=body[0],
        title_path=title[0],
        source=source,
    )
    _REGISTERED = True
    return _BUNDLE
