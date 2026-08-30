"""把一期目录转成 newspaper-layout v0.4 的 articles.json。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from render.edition_model import Article, ImageSpec
from render.parse_edition import EditionMeta, parse_edition_dir

BRIEF_CHARS = 400
LONG_CHARS = 2500

SECTION_PRIORITY = {
    "headline": 0.97,
    "lede": 0.88,
    "deepread": 0.86,
    "oral": 0.80,
    "critical": 0.72,
    "subscribe": 0.60,
    "hotlist": 0.28,
    "health": 0.18,
}

SECTION_EXPORT_ORDER = [
    "headline",
    "lede",
    "deepread",
    "oral",
    "critical",
    "subscribe",
    "hotlist",
    "health",
]

_H1 = re.compile(r"^# (.+)$", re.M)


def edition_to_articles(
    edition_dir: Path,
    *,
    kind: str | None = None,
    lede: str = "",
) -> tuple[list[dict[str, Any]], EditionMeta]:
    edition_dir = Path(edition_dir)
    parsed, meta = parse_edition_dir(edition_dir)
    kind = kind or meta.kind or ("pm" if edition_dir.name.endswith("-pm") else "am")
    ranking = _load_ranking(edition_dir)
    item_bodies = _index_item_files(edition_dir)
    lede_text = lede or _lede_from_file(edition_dir)

    stories: list[Article] = []
    for art in parsed:
        if art.empty:
            continue
        stories.append(art)
    if lede_text:
        stories.append(
            Article(
                id="lede",
                section="lede",
                role="index",
                title="今日综述",
                kicker="综述",
                body=lede_text,
                source="00_lede.md",
            )
        )

    stories.sort(key=lambda a: (SECTION_EXPORT_ORDER.index(a.section) if a.section in SECTION_EXPORT_ORDER else 90, a.priority, a.id))

    prefix = "pm" if kind == "pm" else "am"
    out: list[dict[str, Any]] = []
    for i, art in enumerate(stories, start=1):
        markdown, source = _resolve_body(art, item_bodies)
        images = [_image_dict(img, edition_dir) for img in art.images]
        score = _ranking_score(art, ranking)
        payload = {
            "id": f"{prefix}{i:02d}",
            "title": art.title,
            "markdown": markdown,
            "images": images,
            "priority": _priority(art, i),
            "kind": classify_kind(art.section, art.role, markdown),
            "metadata": {
                "section": art.section,
                "source": source or art.source,
                "ranking_score": score,
            },
        }
        out.append(payload)
    return out, meta


def write_articles_json(
    edition_dir: Path,
    *,
    kind: str | None = None,
    lede: str = "",
    path: Path | None = None,
) -> tuple[Path, list[dict[str, Any]], EditionMeta]:
    edition_dir = Path(edition_dir)
    articles, meta = edition_to_articles(edition_dir, kind=kind, lede=lede)
    dest = path or (edition_dir / "articles.json")
    dest.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return dest, articles, meta


def classify_kind(section: str, role: str, markdown: str) -> str:
    if section == "health":
        return "system_report"
    if section == "lede" or role == "index":
        return "brief"
    n = len(markdown)
    if section == "critical" and n >= LONG_CHARS:
        return "report"
    if n < BRIEF_CHARS:
        return "brief"
    if n >= LONG_CHARS:
        return "long"
    return "normal"


def _priority(art: Article, seq: int) -> float:
    base = SECTION_PRIORITY.get(art.section, 0.5)
    return round(base - (seq * 0.001), 4)


def _ranking_score(art: Article, ranking: dict[str, float]) -> float:
    if art.title in ranking:
        return ranking[art.title]
    for title, score in ranking.items():
        if title and (title in art.title or art.title in title):
            return score
    return 0.2


def _resolve_body(art: Article, item_bodies: list[tuple[str, str, str]]) -> tuple[str, str]:
    """订阅稿优先用 items/*.md;其它栏有同名 item 也用它,否则用解析后的正文。"""
    if art.section in {"lede", "health"} or art.role == "index":
        return art.body.strip(), art.source
    match = _match_item(art.title, item_bodies)
    if match is not None:
        title, body, name = match
        if len(body) >= max(80, int(len(art.body) * 0.6)):
            return body, name
    return art.body.strip(), art.source


def _match_item(
    title: str, item_bodies: list[tuple[str, str, str]]
) -> tuple[str, str, str] | None:
    title = _norm_title(title)
    if not title:
        return None
    for item_title, body, name in item_bodies:
        if _norm_title(item_title) == title:
            return item_title, body, name
    for item_title, body, name in item_bodies:
        nt = _norm_title(item_title)
        if title in nt or nt in title:
            return item_title, body, name
    return None


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", "", title or "").casefold()


def _index_item_files(edition_dir: Path) -> list[tuple[str, str, str]]:
    items_dir = edition_dir / "items"
    if not items_dir.is_dir():
        return []
    out: list[tuple[str, str, str]] = []
    for path in sorted(items_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = _first_h1(text) or path.stem
        body = _clean_item_body(text)
        if body:
            out.append((title, body, path.name))
    return out


def _clean_item_body(text: str) -> str:
    lines: list[str] = []
    seen_title = False
    for line in text.splitlines():
        s = line.strip()
        if not seen_title and s.startswith("# "):
            seen_title = True
            continue
        if s.startswith(">"):
            continue
        if s.startswith("原文地址"):
            continue
        if s.startswith("读完再打点"):
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _lede_from_file(edition_dir: Path) -> str:
    path = edition_dir / "00_lede.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    parts = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith(">"):
            continue
        parts.append(line.rstrip())
    return "\n".join(parts).strip()


def _first_h1(text: str) -> str:
    m = _H1.search(text)
    return m.group(1).strip() if m else ""


def _load_ranking(edition_dir: Path) -> dict[str, float]:
    path = edition_dir / "ranking.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for row in data.get("items") or []:
        title = str(row.get("title") or "").strip()
        if title:
            out[title] = float(row.get("score") or 0.2)
    return out


def _image_dict(img: ImageSpec, edition_dir: Path) -> dict[str, Any]:
    src = img.src or ""
    if src:
        p = Path(src)
        if p.is_absolute():
            try:
                src = str(p.resolve().relative_to(edition_dir.resolve()))
            except ValueError:
                src = str(p)
        elif (edition_dir / src).is_file() and not img.width_px:
            pass
        if (edition_dir / src).is_file():
            try:
                from enrich.images import image_size

                w, h = image_size(edition_dir / src)
                return {
                    "width_px": w,
                    "height_px": h,
                    "src": src,
                    "alt": img.alt,
                    "caption": img.caption,
                }
            except Exception:
                pass
    return {
        "width_px": img.width_px or 1200,
        "height_px": img.height_px or 800,
        "src": src,
        "alt": img.alt,
        "caption": img.caption,
    }
