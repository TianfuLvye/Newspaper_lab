"""Lab 8 入口:一期目录里的 Markdown → A3 矩阵 HTML / PDF。

调试排版只对已有 digest 跑这个模块,不要重跑 collect。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from render.lede import make_lede
from render.layout.engine import layout_edition
from render.layout.model import LayoutResult
from render.parse_edition import parse_edition_dir

log = logging.getLogger("fishnet.newspaper")


@dataclass
class NewspaperResult:
    edition_id: str
    kind: str
    layout: LayoutResult
    html_path: Path
    pdf_path: Path | None
    layout_path: Path
    seconds: float
    error: str | None = None


def render_newspaper(
    edition_dir: Path,
    *,
    kind: str | None = None,
    edition_id: str | None = None,
    pdf: bool = True,
) -> NewspaperResult:
    t0 = time.monotonic()
    edition_dir = Path(edition_dir)
    articles, meta = parse_edition_dir(edition_dir)
    kind = kind or meta.kind or "am"
    eid = edition_id or meta.edition_id or edition_dir.name
    lede, lede_src = make_lede(articles, kind)
    (edition_dir / "00_lede.md").write_text(
        f"# 今日综述\n\n> 来源 `{lede_src}`\n\n{lede}\n",
        encoding="utf-8",
    )
    layout = layout_edition(articles, kind=kind, edition_id=eid, lede=lede)
    layout_path = edition_dir / "layout.json"
    layout_path.write_text(
        json.dumps(layout.to_json_obj(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    from render.html_out import write_html

    html_path = write_html(layout, edition_dir / "digest.html")
    pdf_path: Path | None = None
    err = None
    if pdf:
        try:
            from render.pdf import write_pdf

            pdf_path = write_pdf(layout, edition_dir / "digest.pdf")
        except Exception as e:
            err = repr(e)
            log.warning("[%s] PDF 失败,HTML 仍可用: %s", eid, err)
    dt = time.monotonic() - t0
    log.info(
        "[%s] newspaper pages=%d density=%s html=%s pdf=%s t=%.2fs",
        eid,
        layout.n_pages,
        layout.density,
        html_path,
        pdf_path,
        dt,
    )
    return NewspaperResult(
        edition_id=eid,
        kind=kind,
        layout=layout,
        html_path=html_path,
        pdf_path=pdf_path,
        layout_path=layout_path,
        seconds=dt,
        error=err,
    )
