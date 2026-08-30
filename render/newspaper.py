"""Lab 8 入口:一期目录 → newspaper-layout v0.4 A3 HTML / PDF。

调试排版只对已有 digest 跑这个模块,不要重跑 collect。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.settings import ROOT
from render.edition_to_articles import write_articles_json
from render.lede import make_lede
from render.parse_edition import parse_edition_dir

log = logging.getLogger("fishnet.newspaper")

TEMPLATES_DIR = Path(__file__).resolve().parent / "newspaper_templates"
MEASURE_CACHE = ROOT / "data" / ".cache" / "newspaper-measure.json"


@dataclass
class NewspaperResult:
    edition_id: str
    kind: str
    html_path: Path
    pdf_path: Path | None
    layout_path: Path
    articles_path: Path
    seconds: float
    pages: int = 0
    unassigned: list[str] = field(default_factory=list)
    cost: float = 0.0
    error: str | None = None


def render_newspaper(
    edition_dir: Path,
    *,
    kind: str | None = None,
    edition_id: str | None = None,
    pdf: bool = True,
    exact: bool = True,
    continuations: bool = True,
) -> NewspaperResult:
    t0 = time.monotonic()
    edition_dir = Path(edition_dir)
    parsed, meta = parse_edition_dir(edition_dir)
    kind = kind or meta.kind or "am"
    eid = edition_id or meta.edition_id or edition_dir.name
    lede, lede_src = make_lede(parsed, kind)
    (edition_dir / "00_lede.md").write_text(
        f"# 今日综述\n\n> 来源 `{lede_src}`\n\n{lede}\n",
        encoding="utf-8",
    )
    articles_path, _articles, _meta = write_articles_json(
        edition_dir, kind=kind, lede=lede
    )

    html_path = edition_dir / "digest.html"
    if os.environ.get("FISHNET_SKIP_LAYOUT") == "1":
        return NewspaperResult(
            edition_id=eid,
            kind=kind,
            html_path=html_path,
            pdf_path=None,
            layout_path=edition_dir / "layout.json",
            articles_path=articles_path,
            seconds=time.monotonic() - t0,
            error="skipped",
        )

    layout_path = edition_dir / "layout.json"
    fragments_path = edition_dir / "digest.fragments.json"
    title = "自动日报 · 早报" if kind == "am" else "自动日报 · 晚报"
    date_label = eid.rsplit("-", 1)[0] if eid[-3:] in {"-am", "-pm"} else eid

    plan_obj, n_pages, unassigned, cost = _optimize_render(
        articles_path=articles_path,
        html_path=html_path,
        layout_path=layout_path,
        fragments_path=fragments_path,
        title=title,
        date_label=date_label,
        exact=exact,
        continuations=continuations,
    )
    _ = plan_obj

    pdf_path: Path | None = None
    err = None
    if pdf:
        try:
            pdf_path = _write_pdf(html_path, edition_dir / "digest.pdf")
        except Exception as e:
            err = repr(e)
            log.warning("[%s] PDF 失败,HTML 仍可用: %s", eid, err)

    dt = time.monotonic() - t0
    log.info(
        "[%s] newspaper pages=%d unassigned=%d html=%s pdf=%s t=%.2fs",
        eid,
        n_pages,
        len(unassigned),
        html_path,
        pdf_path,
        dt,
    )
    return NewspaperResult(
        edition_id=eid,
        kind=kind,
        html_path=html_path,
        pdf_path=pdf_path,
        layout_path=layout_path,
        articles_path=articles_path,
        seconds=dt,
        pages=n_pages,
        unassigned=unassigned,
        cost=cost,
        error=err,
    )


def resolve_chromium() -> str:
    env = os.environ.get("CHROMIUM_PATH")
    if env and Path(env).exists():
        return env
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        found = shutil.which(name)
        if found:
            return found
    bundled = _playwright_chromium()
    if bundled:
        return bundled
    raise RuntimeError(
        "找不到 Chromium。先 `uv run playwright install chromium`,或设置 CHROMIUM_PATH。"
    )


def _playwright_chromium() -> str | None:
    roots = [
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    patterns = (
        "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-win64/chrome.exe",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for pat in patterns:
            found = sorted(root.glob(pat))
            if found and found[-1].is_file():
                return str(found[-1])
    return None


def _optimize_render(
    *,
    articles_path: Path,
    html_path: Path,
    layout_path: Path,
    fragments_path: Path,
    title: str,
    date_label: str,
    exact: bool,
    continuations: bool,
) -> tuple[dict, int, list[str], float]:
    from newspaper_layout.chromium_measure import ChromiumArticleMeasurer, ChromiumConfig
    from newspaper_layout.continuation_allocator import ContinuationAllocator
    from newspaper_layout.dom_splitter import DOMSplitter
    from newspaper_layout.matching import SlotMatcher
    from newspaper_layout.measure import ArticleMeasurer
    from newspaper_layout.models import Article as LayoutArticle
    from newspaper_layout.optimizer import LayoutOptimizer, OptimizerConfig
    from newspaper_layout.renderer import HTMLNewspaperRenderer, RenderConfig
    from newspaper_layout.templates import TemplateParser

    if not TEMPLATES_DIR.is_dir():
        raise FileNotFoundError(f"newspaper templates missing: {TEMPLATES_DIR}")

    templates = TemplateParser().load(TEMPLATES_DIR)
    raw = json.loads(articles_path.read_text(encoding="utf-8"))
    for item in raw:
        for image in item.get("images", []):
            src = image.get("src") or image.get("path")
            if src and not str(src).startswith(("http://", "https://", "data:", "file://")):
                candidate = Path(src)
                if not candidate.is_absolute():
                    resolved = (articles_path.parent / candidate).resolve()
                    if resolved.exists():
                        image["src"] = str(resolved)
    original = [LayoutArticle.from_dict(x) for x in raw]

    MEASURE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    chromium = resolve_chromium() if exact or continuations else None

    if exact:
        measurer = ChromiumArticleMeasurer(
            config=ChromiumConfig(executable_path=chromium, cache_path=str(MEASURE_CACHE))
        )
        measurer.start()
        try:
            optimizer = LayoutOptimizer(
                matcher=SlotMatcher(measurer=measurer),
                config=OptimizerConfig(),
            )
            plan = optimizer.optimize(original, templates)
        finally:
            measurer.close()
    else:
        optimizer = LayoutOptimizer(
            matcher=SlotMatcher(measurer=ArticleMeasurer()),
            config=OptimizerConfig(),
        )
        plan = optimizer.optimize(original, templates)

    render_articles = original
    if continuations:
        measurer = ChromiumArticleMeasurer(
            config=ChromiumConfig(executable_path=chromium, cache_path=str(MEASURE_CACHE))
        )
        measurer.start()
        try:
            splitter = DOMSplitter(measurer)
            allocation = ContinuationAllocator(
                LayoutOptimizer(
                    matcher=SlotMatcher(measurer=measurer),
                    config=OptimizerConfig(),
                ),
                splitter,
            ).allocate(plan, original, templates)
            plan = allocation.plan
            render_articles = allocation.articles
        finally:
            measurer.close()

    layout_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fragments_path.write_text(
        json.dumps([a.to_dict() for a in render_articles], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    HTMLNewspaperRenderer(
        RenderConfig(title=title, date_label=date_label, embed_images=True)
    ).render_to_file(html_path, plan, render_articles, templates)
    return plan.to_dict(), len(plan.pages), list(plan.unassigned_article_ids), plan.total_cost


def _write_pdf(html_path: Path, pdf_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    chromium = resolve_chromium()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chromium)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        page.pdf(
            path=str(pdf_path),
            format="A3",
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()
    return pdf_path
