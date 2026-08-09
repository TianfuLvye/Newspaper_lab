"""加载 config/settings.toml 与 sources.yaml。"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.toml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"


@dataclass(frozen=True)
class Settings:
    dailyhot_url: str = "http://127.0.0.1:6688"
    dailyhot_timeout: float = 20.0
    db_path: Path = ROOT / "data" / "fishnet.db"
    render_dir: Path = ROOT / "render" / "sections"


def load_settings(path: Path | None = None) -> Settings:
    p = path or SETTINGS_PATH
    data: dict = {}
    if p.exists():
        with p.open("rb") as f:
            data = tomllib.load(f)
    daily = data.get("dailyhot", {})
    paths = data.get("paths", {})
    return Settings(
        dailyhot_url=str(daily.get("base_url", "http://127.0.0.1:6688")).rstrip("/"),
        dailyhot_timeout=float(daily.get("timeout_seconds", 20)),
        db_path=ROOT / paths.get("db", "data/fishnet.db"),
        render_dir=ROOT / paths.get("render_dir", "render/sections"),
    )


def load_hotlist_sources(path: Path | None = None) -> list[dict]:
    """返回 [{board, source}, ...]。"""
    p = path or SOURCES_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(cfg.get("hotlists") or [])
