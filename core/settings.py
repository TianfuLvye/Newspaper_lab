"""加载 config/settings.toml 与 sources.yaml。"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.toml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"
WECHAT_PATH = ROOT / "config" / "wechat.yaml"


def _expand_feed_rows(rows: list[dict], *, rsshub_url: str) -> list[dict]:
    base = rsshub_url.rstrip("/")
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        url = str(item.get("url", ""))
        item["url"] = url.replace("{rsshub}", base)
        out.append(item)
    return out


def _load_yaml_feeds(path: Path) -> list[dict]:
    if not path.exists():
        return []
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(cfg.get("feeds") or [])


@dataclass(frozen=True)
class Settings:
    dailyhot_url: str = "http://127.0.0.1:6688"
    dailyhot_timeout: float = 20.0
    rsshub_url: str = "http://127.0.0.1:1200"
    db_path: Path = ROOT / "data" / "fishnet.db"
    render_dir: Path = ROOT / "render" / "sections"
    mc_home: Path = ROOT / "third_party" / "MediaCrawler"
    extract_cache_dir: Path = ROOT / "data" / "html_cache"
    extract_cache_ttl_hours: float = 24.0
    extract_delay_seconds: float = 1.5
    extract_min_chars: int = 200
    extract_robots_override_hosts: tuple[str, ...] = (
        "mp.weixin.qq.com",
        "zhuanlan.zhihu.com",
        "www.zhihu.com",
    ),


def load_settings(path: Path | None = None) -> Settings:
    p = path or SETTINGS_PATH
    data: dict = {}
    if p.exists():
        with p.open("rb") as f:
            data = tomllib.load(f)
    daily = data.get("dailyhot", {})
    rsshub = data.get("rsshub", {})
    paths = data.get("paths", {})
    mc = data.get("mediacrawler", {})
    mc_home = Path(str(mc.get("home", "third_party/MediaCrawler")))
    if not mc_home.is_absolute():
        mc_home = ROOT / mc_home
    ex = data.get("extract", {})
    cache_dir = Path(str(ex.get("cache_dir", "data/html_cache")))
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    return Settings(
        dailyhot_url=str(daily.get("base_url", "http://127.0.0.1:6688")).rstrip("/"),
        dailyhot_timeout=float(daily.get("timeout_seconds", 20)),
        rsshub_url=str(rsshub.get("base_url", "http://127.0.0.1:1200")).rstrip("/"),
        db_path=ROOT / paths.get("db", "data/fishnet.db"),
        render_dir=ROOT / paths.get("render_dir", "render/sections"),
        mc_home=mc_home,
        extract_cache_dir=cache_dir,
        extract_cache_ttl_hours=float(ex.get("cache_ttl_hours", 24)),
        extract_delay_seconds=float(ex.get("delay_seconds", 1.5)),
        extract_min_chars=int(ex.get("min_chars", 200)),
        extract_robots_override_hosts=tuple(
            str(h)
            for h in (
                ex.get("robots_override_hosts")
                or ["mp.weixin.qq.com", "zhuanlan.zhihu.com", "www.zhihu.com"]
            )
        ),
    )


def load_hotlist_sources(path: Path | None = None) -> list[dict]:
    """返回 [{board, source}, ...]。"""
    p = path or SOURCES_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(cfg.get("hotlists") or [])


def load_feeds(path: Path | None = None, *, rsshub_url: str | None = None) -> list[dict]:
    """返回已展开 {rsshub} 占位的 feed 配置列表（sources.yaml + 可选 wechat.yaml）。"""
    p = path or SOURCES_PATH
    base = (rsshub_url if rsshub_url is not None else load_settings().rsshub_url).rstrip("/")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rows = list(cfg.get("feeds") or [])
    rows.extend(_load_yaml_feeds(WECHAT_PATH))
    return _expand_feed_rows(rows, rsshub_url=base)


def load_targeted(path: Path | None = None) -> list[dict]:
    """Lab 4:定向采集配置。默认不随全量 collect 启动。"""
    p = path or SOURCES_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [dict(row) for row in (cfg.get("targeted") or [])]
