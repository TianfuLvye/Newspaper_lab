"""手动口播流水线：下载音频 → 火山 STT → Flash 改稿。不改 enrich 网页抽取。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.settings import ROOT
from core.store import Store
from enrich.transcript_copy import to_newspaper
from enrich.transcript_download import download_bilibili_audio
from enrich.transcript_stt import transcribe_audio

BV_RE = re.compile(r"(BV[0-9A-Za-z]+)")
DEFAULT_OUT = ROOT / "data" / "transcripts"


@dataclass
class TranscriptResult:
    bvid: str
    url: str
    title: str
    mode: str
    original_path: Path
    newspaper_path: Path
    layout_path: Path | None
    content_written: bool
    stats: dict


def parse_bvid(raw: str) -> str:
    text = (raw or "").strip()
    match = BV_RE.search(text)
    if not match:
        raise ValueError(f"no BV id in {raw!r}")
    return match.group(1)


def video_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"


def run_transcript(
    video: str,
    *,
    store: Store | None = None,
    out_dir: Path | None = None,
    from_text: Path | None = None,
) -> TranscriptResult:
    bvid = parse_bvid(video)
    url = video_url(bvid)
    dest = Path(out_dir) if out_dir else DEFAULT_OUT
    dest.mkdir(parents=True, exist_ok=True)
    audio_dir = dest / "audio"
    title = bvid
    uploader = ""
    webpage = url
    images: list = []

    if from_text is not None:
        transcript = Path(from_text).read_text(encoding="utf-8")
        if not transcript.strip():
            raise ValueError(f"empty transcript: {from_text}")
    else:
        audio_path, meta = download_bilibili_audio(url, audio_dir)
        title = str(meta.get("title") or title)
        uploader = str(meta.get("uploader") or "")
        webpage = str(meta.get("webpage_url") or url)
        images = list(meta.get("images") or [])
        transcript = transcribe_audio(audio_path)
        (dest / f"{bvid}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    original_path = dest / f"{bvid}.txt"
    original_path.write_text(transcript.rstrip() + "\n", encoding="utf-8")

    item = store.find_bilibili_video(bvid) if store is not None else None
    if item:
        title = title if title != bvid else (item.title or title)
        uploader = uploader or (item.author or "")
        webpage = item.url or webpage

    newspaper, laid_out, stats = to_newspaper(
        transcript=transcript,
        title=title,
        uploader=uploader,
        url=webpage,
    )
    newspaper_path = dest / f"{bvid}.md"
    newspaper_path.write_text(newspaper, encoding="utf-8")
    layout_path = None
    if laid_out:
        layout_path = dest / f"{bvid}.layout.md"
        layout_path.write_text(laid_out, encoding="utf-8")

    content_written = False
    if store is not None and item is not None:
        store.update_content(item.content_hash, newspaper)
        if images:
            have = {str(rec.get("url") or "") for rec in (item.images or [])}
            merged = list(item.images or [])
            for rec in images:
                url_img = rec.get("url") if isinstance(rec, dict) else None
                if url_img and url_img not in have:
                    merged.append(dict(rec))
                    have.add(url_img)
            if merged:
                store.update_images(item.content_hash, merged)
        content_written = True

    return TranscriptResult(
        bvid=bvid,
        url=webpage,
        title=title,
        mode=str(stats.get("mode") or ""),
        original_path=original_path,
        newspaper_path=newspaper_path,
        layout_path=layout_path,
        content_written=content_written,
        stats=stats,
    )
