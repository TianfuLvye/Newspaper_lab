"""口播转写 → 见报稿：短稿改写；长稿先排版再按字数并节抛光。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from core.llm import chat_completions

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
REWRITE_PROMPT = PROMPT_DIR / "transcript_to_newspaper.md"
LAYOUT_PROMPT = PROMPT_DIR / "transcript_layout.md"
POLISH_PROMPT = PROMPT_DIR / "transcript_polish.md"
SHORT_LIMIT = 3000
POLISH_BATCH = 4000
SENTENCE_SPLIT = re.compile(r"(?<=[。！？])")


def char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def flatten(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def split_sentences(text: str) -> list[str]:
    flat = flatten(text)
    parts = [p for p in SENTENCE_SPLIT.split(flat) if p]
    return parts or ([flat] if flat else [])


def rewrite_short(*, transcript: str, title: str, uploader: str, url: str) -> str:
    user = (
        f"标题: {title}\n"
        f"UP 主: {uploader}\n"
        f"链接: {url}\n\n"
        f"口播转写:\n{transcript}\n"
    )
    text = chat_completions(
        system=REWRITE_PROMPT.read_text(encoding="utf-8").strip(),
        user=user,
        temperature=0.2,
        timeout=180.0,
        thinking=True,
    )
    return text.rstrip() + "\n"


def parse_layout(raw: str, n: int) -> dict:
    blob = raw
    start = blob.find("{")
    end = blob.rfind("}")
    if start >= 0 and end > start:
        blob = blob[start : end + 1]
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise TypeError("layout result is not an object")
    guide = str(data.get("guide") or "").strip()
    if not guide:
        raise ValueError("layout guide is empty")

    breaks: set[int] = set()
    for item in data.get("paragraph_before") or []:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if 2 <= idx <= n:
            breaks.add(idx)

    sections: list[tuple[int, str]] = []
    seen: set[int] = set()
    for row in data.get("sections") or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("before"))
        except (TypeError, ValueError):
            continue
        heading = str(row.get("title") or "").strip()
        if not heading or idx < 1 or idx > n or idx in seen:
            continue
        seen.add(idx)
        sections.append((idx, heading[:14]))
    sections.sort(key=lambda item: item[0])
    return {"guide": guide, "paragraph_before": breaks, "sections": sections}


def render_long(*, sentences: list[str], title: str, layout: dict) -> str:
    """段内句子连成一行；只有双换行才分段。"""
    section_at = {idx: name for idx, name in layout["sections"]}
    parts: list[str] = [f"# {title}", "", f"**{layout['guide']}**", ""]
    para: list[str] = []

    def flush_para() -> None:
        if not para:
            return
        if parts and parts[-1] != "":
            parts.append("")
        parts.append("".join(para))
        para.clear()

    for i, sent in enumerate(sentences, start=1):
        if i in section_at:
            flush_para()
            if parts and parts[-1] != "":
                parts.append("")
            parts.append(f"## {section_at[i]}")
            parts.append("")
        elif para and i in layout["paragraph_before"]:
            flush_para()
        para.append(sent)
    flush_para()
    return "\n".join(parts).rstrip() + "\n"


def layout_long(*, transcript: str, title: str, uploader: str, url: str) -> str:
    sentences = split_sentences(transcript)
    numbered = "\n".join(f"S{i} {sent}" for i, sent in enumerate(sentences, start=1))
    user = (
        f"标题: {title}\n"
        f"UP 主: {uploader}\n"
        f"链接: {url}\n"
        f"句数: {len(sentences)}\n\n"
        f"{numbered}\n"
    )
    raw = chat_completions(
        system=LAYOUT_PROMPT.read_text(encoding="utf-8").strip(),
        user=user,
        temperature=0.2,
        timeout=300.0,
        thinking=False,
    )
    layout = parse_layout(raw, len(sentences))
    if flatten("".join(sentences)) != flatten(transcript):
        raise RuntimeError("sentence split dropped or altered characters")
    return render_long(sentences=sentences, title=title, layout=layout)


def split_paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]


def pack_batches(paras: list[str], limit: int = POLISH_BATCH) -> list[list[str]]:
    batches: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        n = char_count(p)
        if cur and size + n > limit:
            batches.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        batches.append(cur)
    return batches


def extract_json_obj(raw: str) -> dict:
    blob = raw
    start = blob.find("{")
    end = blob.rfind("}")
    if start >= 0 and end > start:
        blob = blob[start : end + 1]
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise TypeError("polish result is not an object")
    return data


class Sec:
    __slots__ = ("heading", "paras", "sid")

    def __init__(self, sid: int, heading: str, paras: list[str]) -> None:
        self.sid = sid
        self.heading = heading
        self.paras = paras

    @property
    def chars(self) -> int:
        return sum(char_count(p) for p in self.paras)


def parse_layout_sections(rest: str) -> list[Sec]:
    chunks = re.split(r"(?=^## )", rest, flags=re.MULTILINE)
    secs: list[Sec] = []
    sid = 0
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        heading = ""
        body = chunk
        if chunk.startswith("## "):
            line, _, body = chunk.partition("\n")
            heading = line.strip()
            body = body.lstrip("\n")
        paras = split_paragraphs(body)
        if not paras:
            if heading:
                secs.append(Sec(sid, heading, []))
                sid += 1
            continue
        secs.append(Sec(sid, heading, paras))
        sid += 1
    return secs


def pack_section_jobs(secs: list[Sec], limit: int = POLISH_BATCH) -> list[list[Sec]]:
    """相邻小节拼到 limit 字；单节超限则按段切，且不与其它节混装。"""
    jobs: list[list[Sec]] = []
    cur: list[Sec] = []
    size = 0
    for sec in secs:
        if not sec.paras:
            continue
        if sec.chars > limit:
            if cur:
                jobs.append(cur)
                cur, size = [], 0
            for paras in pack_batches(sec.paras, limit):
                jobs.append([Sec(sec.sid, sec.heading, paras)])
            continue
        if cur and size + sec.chars > limit:
            jobs.append(cur)
            cur, size = [], 0
        cur.append(sec)
        size += sec.chars
    if cur:
        jobs.append(cur)
    return jobs


def merge_slices(slices: list[Sec]) -> list[Sec]:
    out: list[Sec] = []
    for sec in slices:
        if out and out[-1].sid == sec.sid:
            out[-1].paras.extend(sec.paras)
            continue
        out.append(Sec(sec.sid, sec.heading, list(sec.paras)))
    return out


def _clean_para(text: str, index: int) -> str:
    text = re.sub(rf"^P{index}\s+", "", str(text).strip())
    return re.sub(r"\s*\n\s*", "", text).strip()


def polish_sections(secs: list[Sec]) -> list[Sec]:
    blocks = []
    for i, sec in enumerate(secs, start=1):
        paras = "\n\n".join(f"P{j} {p}" for j, p in enumerate(sec.paras, start=1))
        blocks.append(
            f"S{i} heading: {sec.heading or '（无）'}\n段数: {len(sec.paras)}\n\n{paras}"
        )
    user = f"节数: {len(secs)}\n\n" + "\n\n".join(blocks) + "\n"
    raw = chat_completions(
        system=POLISH_PROMPT.read_text(encoding="utf-8").strip(),
        user=user,
        temperature=0.1,
        timeout=300.0,
        thinking=True,
    )
    data = extract_json_obj(raw)
    got = data.get("sections")
    if not isinstance(got, list) or len(got) != len(secs):
        raise ValueError(
            f"polish section count {len(got) if isinstance(got, list) else got!r} != {len(secs)}"
        )
    out: list[Sec] = []
    for i, (sec, row) in enumerate(zip(secs, got), start=1):
        if not isinstance(row, dict):
            raise TypeError(f"S{i} is not an object")
        heading = str(row.get("heading") or "").strip()
        if heading != sec.heading:
            raise ValueError(f"S{i} heading changed {sec.heading!r} -> {heading!r}")
        paras = row.get("paragraphs")
        if not isinstance(paras, list) or len(paras) != len(sec.paras):
            n = len(paras) if isinstance(paras, list) else paras
            raise ValueError(f"S{i} paragraph count {n!r} != {len(sec.paras)}")
        cleaned = [_clean_para(p, j) for j, p in enumerate(paras, start=1)]
        if any(not p for p in cleaned):
            raise ValueError(f"S{i} returned an empty paragraph")
        out.append(Sec(sec.sid, sec.heading, cleaned))
    old_n = sum(s.chars for s in secs)
    new_n = sum(s.chars for s in out)
    if new_n < old_n * 0.75 or new_n > old_n * 1.08:
        raise ValueError(f"polish length jumped {old_n} -> {new_n}")
    return out


def polish_job(secs: list[Sec], stats: dict) -> list[Sec]:
    stats["batches"] += 1
    labels = [s.heading or "(intro)" for s in secs]
    print(
        f"polish {stats['batches']}: {len(secs)} sections / {sum(s.chars for s in secs)} chars "
        f"({', '.join(labels[:4])}{'…' if len(labels) > 4 else ''})",
        file=sys.stderr,
        flush=True,
    )
    try:
        got = polish_sections(secs)
        stats["ok"] += 1
        return got
    except Exception as exc:  # noqa: BLE001 — LLM/JSON 失败都应对半拆或回退该节
        if len(secs) == 1:
            stats["fallback"] += 1
            print(f"polish fallback {labels[0]}: {exc}", file=sys.stderr, flush=True)
            return secs
        mid = max(1, len(secs) // 2)
        print(
            f"polish retry split {len(secs)} -> {mid}+{len(secs) - mid}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return polish_job(secs[:mid], stats) + polish_job(secs[mid:], stats)


def polish_long(md: str) -> tuple[str, dict]:
    """对 layout 中间稿按字数合并小节后抛光。导读、节标题和换行结构不动。"""
    parts = md.strip().split("\n\n", 2)
    if len(parts) < 3 or not parts[0].startswith("# ") or not parts[1].startswith("**"):
        raise ValueError("layout markdown missing title/guide")
    title, guide, rest = parts[0], parts[1], parts[2].strip()
    secs = parse_layout_sections(rest)
    stats = {"batches": 0, "ok": 0, "fallback": 0}
    cleaned: list[Sec] = []
    for job in pack_section_jobs(secs):
        cleaned.extend(polish_job(job, stats))
    by_sid = {s.sid: s for s in merge_slices(cleaned)}
    polished: list[str] = [title, "", guide, ""]
    for sec in secs:
        got = by_sid.get(sec.sid, sec)
        if got.heading:
            body = "\n\n".join(got.paras)
            polished.extend([got.heading, "", body, ""] if body else [got.heading, ""])
        else:
            polished.extend(["\n\n".join(got.paras), ""])
    return "\n".join(polished).rstrip() + "\n", stats


def to_newspaper(
    *,
    transcript: str,
    title: str,
    uploader: str = "",
    url: str = "",
) -> tuple[str, str | None, dict]:
    """返回 (见报稿, layout 中间稿或 None, 统计)。"""
    nchars = char_count(transcript)
    stats: dict = {"chars": nchars}
    if nchars < SHORT_LIMIT:
        text = rewrite_short(
            transcript=transcript, title=title, uploader=uploader, url=url
        )
        stats["mode"] = "rewrite"
        return text, None, stats
    laid_out = layout_long(
        transcript=transcript, title=title, uploader=uploader, url=url
    )
    text, polish_stats = polish_long(laid_out)
    stats["mode"] = "layout"
    stats.update(polish_stats)
    return text, laid_out, stats
