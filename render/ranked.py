"""Lab 7 个性化版面:头版 / 深度阅读 / 今日一问。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.schema import Kind
from core.text import display_title, readable_body

CST = timezone(timedelta(hours=8))


def _score_line(ri) -> str:
    p = ri.breakdown.parts
    llm = ri.critic
    bits = [
        f"总分 {ri.total:.2f}",
        f"sim {p.get('sim', 0):.2f}",
        f"len {p.get('len', 0):.2f}",
        f"kw {p.get('kw', 0):.2f}",
        f"hot {p.get('hot', 0):.2f}",
    ]
    if llm:
        bits.append(f"批判 {llm.raw:.0f}/10")
    return " · ".join(bits)


def _feedback_hint(edition_id: str, n: int) -> str:
    if edition_id.startswith("ab-"):
        return (
            "这是 A/B 对照稿,不要对 `ab-am` 做 feedback。"
            "等 `render --edition am` 出正式报纸后,用那期的期号和编号打点。"
        )
    return (
        f"读完再打点: `uv run main.py feedback --edition {edition_id} --n {n} --label 1` 有用, "
        f"`--label -1` 无用。没读不要标。"
    )


def render_ranked_section(
    title: str,
    rows: list,
    *,
    edition_id: str,
    start_n: int,
    intro: str,
    images=None,
) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    lines = [
        f"# {title}",
        "",
        f"> {intro}",
        f"> 生成于 {now} · 编号供读完打点,不需要从纸上点链接。",
        "",
    ]
    if not rows:
        lines.append("_本栏目今日无入选。_")
        lines.append("")
        return "\n".join(lines)

    n = start_n
    for ri in rows:
        it = ri.item
        lines.append(f"## F{n:02d} · {display_title(it)}")
        lines.append("")
        lines.append(f"> {_score_line(ri)}")
        if ri.critic and ri.critic.angle:
            lines.append(f"> 挑战了: {ri.critic.angle} — {ri.critic.reason}")
        lines.append("")
        body = readable_body(it)
        if it.kind == Kind.VIDEO:
            lines.append("_视频暂不转写。_")
            lines.append("")
        if images is not None:
            block = images.markdown_for(it)
            if block:
                lines.append(block.rstrip())
                lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        else:
            lines.append("_没有正文,只保留标题。_")
            lines.append("")
        if ri.related:
            names = "；".join(x.title[:40] for x in ri.related[:5])
            lines.append(f"**相关报道(已折叠):** {names}")
            lines.append("")
        if it.url and it.url.startswith("http"):
            lines.append(f"原文地址(需上网): `{it.url}`")
            lines.append("")
        lines.append(_feedback_hint(edition_id, n))
        lines.append("")
        lines.append("---")
        lines.append("")
        n += 1
    return "\n".join(lines).rstrip() + "\n"


def render_headline(result, *, edition_id: str, images=None) -> str:
    return render_ranked_section(
        "头版",
        result.headline,
        edition_id=edition_id,
        start_n=1,
        intro="今日打分最高的几条,不是热度最高的几条。",
        images=images,
    )


def render_deepread(result, *, edition_id: str, images=None) -> str:
    return render_ranked_section(
        "深度阅读",
        result.deepread,
        edition_id=edition_id,
        start_n=1 + len(result.headline),
        intro="更像收藏夹里那种长度和口味的长文。",
        images=images,
    )


def render_critical(result, *, edition_id: str, images=None) -> str:
    return render_ranked_section(
        "今日一问",
        result.critical,
        edition_id=edition_id,
        start_n=1 + len(result.headline) + len(result.deepread),
        intro="批判性思考分最高的内容。允许它和你的口味不太像。",
        images=images,
    )


def render_ab_markdown(
    *,
    edition_id: str,
    heat_items: list,
    scored_titles: list[str],
    heat_titles: list[str],
) -> str:
    lines = [
        f"# A/B 盲评对照 · {edition_id}",
        "",
        "> 先看两组标题,再决定哪期更想读。看完之前不要翻打分版和热度版的文件名含义。",
        "> 本组实验:A = 纯热度,B = 个性化打分(含探索与折叠)。",
        "",
        "## 组 A",
        "",
    ]
    for i, t in enumerate(heat_titles, 1):
        lines.append(f"{i}. {t}")
    lines += ["", "## 组 B", ""]
    for i, t in enumerate(scored_titles, 1):
        lines.append(f"{i}. {t}")
    lines += [
        "",
        "## 怎么评",
        "",
        "遮住上面的说明,问自己:早餐更想读哪一组?记下 A 或 B,再回来对照文件 `heat.md` / `scored.md`。",
        "",
    ]
    return "\n".join(lines)
