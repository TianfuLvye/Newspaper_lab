"""Lab 4 · 小红书创作者采集 —— 子进程隔离 MediaCrawler。

设计约束:
- 不 import MediaCrawler(Playwright + Chromium 不能进主进程)
- 默认 6 小时一轮、并发 = 1
- 抓不到就抛异常,绝不 return []
- 测试可注入 jsonl_path,不启动浏览器
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from core.base import BaseCollector
from core.schema import Item, Kind, Source
from core.settings import ROOT, load_settings

_TAG_SPLIT = re.compile(r"[,，、|]+")


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[\s/\\]+", "_", s)
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s, flags=re.UNICODE)
    return s or "unnamed"


def _ms_to_dt(raw) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 1e14:  # 微秒
        ts /= 1e6
    elif ts > 1e11:  # 毫秒
        ts /= 1e3
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _to_heat(raw) -> float | None:
    if raw is None or raw == "":
        return None
    s = str(raw).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _tags(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t for t in _TAG_SPLIT.split(str(raw)) if t.strip()]


def row_to_item(row: dict, *, collector: str) -> Item | None:
    """把 MediaCrawler jsonl 行收成 Item。兼容落库字段与原始 note 结构。"""
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    interact = row.get("interact_info") if isinstance(row.get("interact_info"), dict) else {}

    title = str(row.get("title") or row.get("desc") or "").strip()
    note_id = str(row.get("note_id") or row.get("id") or "").strip()
    url = str(row.get("note_url") or row.get("url") or "").strip()
    if not url and note_id:
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if not title and not url:
        return None

    desc = str(row.get("desc") or row.get("summary") or "").strip()
    author = str(
        row.get("nickname") or user.get("nickname") or row.get("author") or ""
    ).strip() or None
    author_id = str(
        row.get("user_id")
        or row.get("creator_hash")
        or user.get("user_id")
        or ""
    ).strip() or None
    heat = _to_heat(
        row.get("liked_count")
        or interact.get("liked_count")
        or row.get("heat")
    )
    published = _ms_to_dt(row.get("time") or row.get("last_update_time"))

    return Item(
        source=Source.XHS,
        kind=Kind.POST,
        title=title or url,
        url=url or f"urn:xhs:{collector}:{title}",
        summary=(desc[:500] or None),
        author=author,
        author_id=author_id,
        published_at=published,
        heat=heat,
        tags=_tags(row.get("tag_list") or row.get("tags")),
        collector=collector,
        raw=row,
    )


def iter_jsonl(path: Path) -> Iterable[dict]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise RuntimeError(f"json 根节点不是数组: {path}")
        for row in data:
            if isinstance(row, dict):
                yield row
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            yield row


def latest_jsonl(dirpath: Path) -> Path | None:
    if not dirpath.exists():
        return None
    files = sorted(dirpath.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    files += sorted(dirpath.rglob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


class XHSCreatorCollector(BaseCollector):
    """指定小红书创作者。重依赖留在子进程,主进程只读 jsonl。"""

    interval_minutes = 360
    max_concurrency = 1

    def __init__(
        self,
        cfg: dict,
        *,
        jsonl_path: Path | None = None,
        mc_home: Path | None = None,
        out_dir: Path | None = None,
    ):
        self.cfg = cfg
        self.name = f"xhs_{slugify(str(cfg.get('name') or 'creator'))}"
        if cfg.get("interval_minutes"):
            self.interval_minutes = int(cfg["interval_minutes"])
        self.max_concurrency = int(cfg.get("max_concurrency") or 1)
        if self.max_concurrency != 1:
            raise ValueError("Lab 4 强制并发 = 1,禁止调高")
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._mc_home = Path(mc_home) if mc_home else load_settings().mc_home
        self._out_dir = Path(out_dir) if out_dir else (ROOT / "data" / "mc_out" / self.name)

    def collect(self) -> Iterable[Item]:
        path = self._jsonl_path or self._run_mediacrawler()
        items = []
        for row in iter_jsonl(path):
            it = row_to_item(row, collector=self.name)
            if it:
                items.append(it)
        if not items:
            raise RuntimeError(f"{self.name} jsonl 无有效笔记: {path}")
        yield from items

    def _run_mediacrawler(self) -> Path:
        creator_id = str(self.cfg.get("creator_id") or "").strip()
        if not creator_id:
            raise RuntimeError(
                f"{self.name} 未配置 creator_id。在 config/sources.yaml 的 targeted "
                "里填小红书 user_id 或主页 URL,并先完成本机 MediaCrawler 扫码登录。"
            )
        home = self._mc_home
        if not (home / "main.py").exists():
            raise RuntimeError(
                f"MediaCrawler 不在 {home}。请先:\n"
                "  git clone --depth 1 https://github.com/NanmiCoder/MediaCrawler "
                "third_party/MediaCrawler\n"
                "  cd third_party/MediaCrawler && uv sync && uv run playwright install chromium"
            )
        out = self._out_dir
        out.mkdir(parents=True, exist_ok=True)
        max_notes = int(self.cfg.get("max_notes") or 10)
        cmd = [
            "uv",
            "run",
            "main.py",
            "--platform",
            "xhs",
            "--lt",
            "qrcode",
            "--type",
            "creator",
            "--creator_id",
            creator_id,
            "--save_data_option",
            "jsonl",
            "--get_comment",
            "false",
            "--max_concurrency_num",
            "1",
            "--crawler_max_notes_count",
            str(max_notes),
            "--save_data_path",
            str(out.resolve()),
            "--headless",
            "false",
        ]
        # 不捕获输出:二维码 / Chrome 远程调试确认必须出现在当前终端。
        proc = subprocess.run(
            cmd,
            cwd=home,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"MediaCrawler 退出码 {proc.returncode}。"
                "需要本机扫码登录;若上游默认开了 CDP,请先在 Chrome 打开远程调试,"
                "或在 third_party/MediaCrawler/config/base_config.py 把 "
                "ENABLE_CDP_MODE 设为 False。"
            )
        path = latest_jsonl(out)
        if path is None:
            raise RuntimeError(f"MediaCrawler 未写出 jsonl: {out}")
        return path
