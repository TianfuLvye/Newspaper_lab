"""头版「今日综述」。手册要求读 Top 20 写约 200 字;没 API 时用抽句兜底。"""
from __future__ import annotations

import os
import re

from render.layout.model import Article

LEDE_PROMPT = """你是这份个人报纸的夜班编辑。根据下面的标题和开头,写一段约 200 字的「今日综述」。
要求:像报纸社论导语,不要条目列表,不要营销腔,点出 2–4 条主线之间的关系。只输出综述正文。
"""


def first_sentence(text: str, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?"):
        i = text.find(sep)
        if 8 <= i <= limit:
            return text[: i + 1]
    for sep in ("，", "、", ",", "；", ";"):
        i = text.find(sep)
        if 16 <= i <= limit:
            return text[:i] + "。"
    return text[:limit].rstrip("…。 ") + "。"


def extractive_lede(articles: list[Article], kind: str) -> str:
    heads = [a for a in articles if a.section == "headline" and not a.empty][:3]
    pool = [
        a
        for a in articles
        if a.role == "story" and not a.empty and a.section != "health"
    ][:20]
    if not pool and not heads:
        label = "早报" if kind == "am" else "晚报"
        return f"本期{label}稿件不足,请先看体检页,再决定要不要当它是一份报。"
    bits = []
    for a in heads:
        sent = first_sentence(a.body, 56)
        if sent:
            bits.append(sent if sent.endswith(("。", "！", "？", ".", "!", "?")) else sent + "。")
        else:
            bits.append(a.title[:22].rstrip("。") + "。")
    lead = "".join(bits)
    extra = [a.title[:14] for a in pool if a not in heads][:3]
    if extra and len(lead) < 140:
        lead = lead.rstrip("。") + "。内页还有" + "、".join(extra) + "。"
    label = "今晨" if kind == "am" else "今日"
    text = f"{label}：{lead}".strip()
    return text[:180]


def llm_lede(articles: list[Article], kind: str, *, timeout: float = 12.0) -> str | None:
    key = os.environ.get("FISHNET_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    pool = [
        a
        for a in articles
        if a.role == "story" and not a.empty
    ][:20]
    if not pool:
        return None
    lines = []
    for a in pool:
        lines.append(f"- [{a.section}] {a.title}\n  {first_sentence(a.body, 90)}")
    payload = {
        "model": os.environ.get("FISHNET_LLM_MODEL") or "gpt-4o-mini",
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": LEDE_PROMPT.strip()},
            {
                "role": "user",
                "content": ("早报" if kind == "am" else "晚报") + "候选:\n" + "\n".join(lines),
            },
        ],
    }
    try:
        import httpx

        base = (
            os.environ.get("FISHNET_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```.*|```$", "", content).strip()
        return content[:400] or None
    except Exception:
        return None


def make_lede(articles: list[Article], kind: str) -> tuple[str, str]:
    """返回 (综述, 来源 heuristic/llm)。失败永不抛。"""
    got = llm_lede(articles, kind)
    if got:
        return got, "llm"
    return extractive_lede(articles, kind), "extractive"
