"""B 站音频下载。只抽音轨，需要本机 ffmpeg。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

BILI_HTTP_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def bilibili_ydl_opts(dest_dir: Path) -> dict[str, Any]:
    """yt-dlp 选项。B 站 playurl 无 Referer/Origin 时常 412。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "proxy": "",
        "http_headers": dict(BILI_HTTP_HEADERS),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
        ],
    }


def _resolve_audio_path(ydl: Any, info: dict[str, Any]) -> Path:
    requested = info.get("requested_downloads") or []
    for row in requested:
        filepath = row.get("filepath")
        if filepath:
            path = Path(filepath)
            if path.exists():
                return path
            m4a = path.with_suffix(".m4a")
            if m4a.exists():
                return m4a
    prepared = Path(ydl.prepare_filename(info))
    for candidate in (
        prepared.with_suffix(".m4a"),
        prepared,
        prepared.with_suffix(".wav"),
        prepared.with_suffix(".mp3"),
    ):
        if candidate.exists():
            return candidate
    raise RuntimeError(f"yt-dlp finished but no audio file near {prepared}")


def download_bilibili_audio(url: str, dest_dir: Path) -> tuple[Path, dict[str, Any]]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run `uv sync`.") from exc

    opts = bilibili_ydl_opts(dest_dir)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and "entries" in info and info["entries"]:
                info = info["entries"][0]
            info = ydl.sanitize_info(info or {})
            audio_path = _resolve_audio_path(ydl, info)
    except Exception as exc:
        msg = str(exc)
        if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower():
            raise RuntimeError(
                "ffmpeg is required to extract audio. Install ffmpeg and retry."
            ) from exc
        raise

    meta = {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration"),
        "id": info.get("id") or "",
        "webpage_url": info.get("webpage_url") or url,
        "images": thumbnail_candidates(info),
    }
    return audio_path, meta


def thumbnail_candidates(info: dict[str, Any], *, limit: int = 2) -> list[dict[str, str]]:
    """封面 + 可选另一张静帧 URL。同一图不同尺寸只留一张。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: object, role: str) -> None:
        raw = str(url or "").strip()
        if not raw or not raw.startswith("http"):
            return
        key = raw.split("@", 1)[0]
        if key in seen:
            return
        seen.add(key)
        out.append({"url": raw, "alt": str(info.get("title") or ""), "role": role})

    add(info.get("thumbnail"), "cover")
    for row in info.get("thumbnails") or []:
        if isinstance(row, dict):
            add(row.get("url"), "cover" if not out else "body")
        if len(out) >= limit:
            return out[:limit]
    add(info.get("screenshot"), "body")
    add(info.get("first_frame"), "body")
    return out[:limit]
