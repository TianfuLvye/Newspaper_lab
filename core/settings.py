"""加载 config/settings.toml 与 sources.yaml。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.toml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"
WECHAT_PATH = ROOT / "config" / "wechat.yaml"
OVERLAY_PATH = ROOT / "config" / "overlay.yaml"
BILIBILI_PATH = ROOT / "config" / "bilibili.yaml"


def load_env_file(path: Path | None = None) -> None:
    """把仓库根目录 .env 填进 os.environ（已有的键不覆盖）。uv run 也会自动加载。"""
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = value.strip().strip("'").strip('"')


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
    )
    editions_dir: Path = ROOT / "data" / "editions"
    scheduler_tz: str = "Asia/Shanghai"
    scheduler_jitter_seconds: int = 120
    enrich_interval_hours: int = 6
    enrich_limit: int = 40
    edition_am_hour: int = 7
    edition_am_minute: int = 0
    edition_pm_hour: int = 18
    edition_pm_minute: int = 0
    wewe_url: str = "http://127.0.0.1:4000"


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
    sched = data.get("scheduler", {})
    wewe = data.get("wewe", {})

    def _abs(raw: str) -> Path:
        pth = Path(raw)
        return pth if pth.is_absolute() else ROOT / pth

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
        editions_dir=_abs(paths.get("editions_dir", "data/editions")),
        scheduler_tz=str(sched.get("timezone", "Asia/Shanghai")),
        scheduler_jitter_seconds=int(sched.get("jitter_seconds", 120)),
        enrich_interval_hours=int(sched.get("enrich_interval_hours", 6)),
        enrich_limit=int(sched.get("enrich_limit", 40)),
        edition_am_hour=int(sched.get("am_hour", 7)),
        edition_am_minute=int(sched.get("am_minute", 0)),
        edition_pm_hour=int(sched.get("pm_hour", 18)),
        edition_pm_minute=int(sched.get("pm_minute", 0)),
        wewe_url=str(wewe.get("base_url", "http://127.0.0.1:4000")).rstrip("/"),
    )


def load_hotlist_sources(path: Path | None = None) -> list[dict]:
    """返回 [{board, source}, ...]。"""
    p = path or SOURCES_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(cfg.get("hotlists") or [])


def empty_overlay() -> dict:
    return {"feeds": [], "replacements": [], "disabled": []}


def load_overlay(path: Path | None = None) -> dict:
    """控制台 overlay：新增 feeds、按 name 覆盖内置字段、disabled 隐藏内置源。"""
    p = path or OVERLAY_PATH
    if not p.exists():
        return empty_overlay()
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {
        "feeds": [dict(row) for row in (cfg.get("feeds") or [])],
        "replacements": [dict(row) for row in (cfg.get("replacements") or [])],
        "disabled": [str(x) for x in (cfg.get("disabled") or [])],
    }


def apply_overlay(builtin: list[dict], overlay: dict | None) -> list[dict]:
    """把 overlay 套到 sources.yaml 的 feeds 上（不含 wechat）。"""
    ov = overlay or empty_overlay()
    disabled = {str(n) for n in ov.get("disabled") or []}
    repl: dict[str, dict] = {}
    for row in ov.get("replacements") or []:
        name = str(row.get("name") or "")
        if name:
            repl[name] = dict(row)

    out: list[dict] = []
    seen: set[str] = set()
    for row in builtin:
        name = str(row.get("name") or "")
        if not name or name in disabled:
            continue
        merged = dict(row)
        extra = dict(repl.get(name) or {})
        extra.pop("name", None)
        merged.update(extra)
        merged["name"] = name
        out.append(merged)
        seen.add(name)

    for row in ov.get("feeds") or []:
        name = str(row.get("name") or "")
        if not name or name in disabled or name in seen:
            continue
        out.append(dict(row))
        seen.add(name)
    return out


def load_feeds(
    path: Path | None = None,
    *,
    rsshub_url: str | None = None,
    overlay_path: Path | None = None,
    wechat_path: Path | None = None,
) -> list[dict]:
    """返回已展开 {rsshub} 占位的 feed 列表（sources + overlay + wechat）。"""
    p = path or SOURCES_PATH
    base = (rsshub_url if rsshub_url is not None else load_settings().rsshub_url).rstrip("/")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    builtin = [dict(row) for row in (cfg.get("feeds") or [])]
    overlay = load_overlay(overlay_path)
    rows = apply_overlay(builtin, overlay)
    disabled = {str(n) for n in overlay.get("disabled") or []}
    for row in _load_yaml_feeds(wechat_path or WECHAT_PATH):
        name = str(row.get("name") or "")
        if name and name not in disabled:
            rows.append(dict(row))
    return _expand_feed_rows(rows, rsshub_url=base)


def load_targeted(path: Path | None = None) -> list[dict]:
    """Lab 4:定向采集配置。默认不随全量 collect 启动。"""
    p = path or SOURCES_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [dict(row) for row in (cfg.get("targeted") or [])]


def load_bilibili_whitelist(path: Path | None = None) -> dict:
    """B 站口播白名单。文件不存在则两类都为空。"""
    p = path or BILIBILI_PATH
    if not p.exists():
        return {"ups": [], "collections": []}
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {
        "ups": list(cfg.get("ups") or []),
        "collections": list(cfg.get("collections") or []),
    }
