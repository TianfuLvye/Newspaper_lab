"""读写 overlay.yaml / wechat.yaml。不碰 sources.yaml 正文。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from collectors.rss_generic import slugify
from core.settings import (
    OVERLAY_PATH,
    SOURCES_PATH,
    WECHAT_PATH,
    apply_overlay,
    empty_overlay,
    load_overlay,
    load_settings,
)

EDITABLE = ("url", "source", "kind", "weight", "title_regex", "interval_minutes")

OVERLAY_HEADER = """# 订阅控制台 overlay（由 `uv run main.py console` 写入）
#
# feeds: 新加的知乎 / 通用 RSS
# replacements: 覆盖 sources.yaml 里同名源的字段
# disabled: 隐藏内置源（不改 sources.yaml）
"""

WECHAT_HEADER = """# 微信公众号订阅（由 `uv run main.py console` 写入）
#
# 前置: WeWe RSS 已登录微信读书。也可手改本文件。
"""


class FeedStoreError(ValueError):
    pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _dump(data: dict) -> str:
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return body


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _feed_row(row: dict) -> dict:
    out: dict = {
        "name": str(row["name"]),
        "url": str(row["url"]),
        "source": str(row.get("source") or "rss"),
        "kind": str(row.get("kind") or "article"),
    }
    if row.get("weight") is not None and row.get("weight") != "":
        out["weight"] = float(row["weight"])
    if row.get("title_regex"):
        out["title_regex"] = str(row["title_regex"])
    if row.get("title_exclude_regex"):
        out["title_exclude_regex"] = str(row["title_exclude_regex"])
    if row.get("interval_minutes"):
        out["interval_minutes"] = int(row["interval_minutes"])
    return out


def category_of(source: str) -> str:
    if source == "zhihu":
        return "zhihu"
    if source == "wechat_mp":
        return "wechat"
    if source == "finance":
        return "finance"
    return "other"


@dataclass
class FeedPaths:
    sources: Path = SOURCES_PATH
    overlay: Path = OVERLAY_PATH
    wechat: Path = WECHAT_PATH
    rsshub_url: str = field(default_factory=lambda: load_settings().rsshub_url)


@dataclass
class FeedView:
    name: str
    url: str
    url_expanded: str
    source: str
    kind: str
    weight: float | None
    title_regex: str | None
    title_exclude_regex: str | None
    interval_minutes: int | None
    origin: str
    enabled: bool
    collector: str
    category: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "url_expanded": self.url_expanded,
            "source": self.source,
            "kind": self.kind,
            "weight": self.weight,
            "title_regex": self.title_regex,
            "title_exclude_regex": self.title_exclude_regex,
            "interval_minutes": self.interval_minutes,
            "origin": self.origin,
            "enabled": self.enabled,
            "collector": self.collector,
            "category": self.category,
        }


class FeedStore:
    def __init__(self, paths: FeedPaths | None = None):
        self.paths = paths or FeedPaths()

    def _builtin_feeds(self) -> list[dict]:
        cfg = _load_yaml(self.paths.sources)
        return [dict(row) for row in (cfg.get("feeds") or [])]

    def _overlay(self) -> dict:
        return load_overlay(self.paths.overlay)

    def _wechat_feeds(self) -> list[dict]:
        cfg = _load_yaml(self.paths.wechat)
        return [dict(row) for row in (cfg.get("feeds") or [])]

    def write_overlay(self, overlay: dict) -> None:
        data = {
            "feeds": [_feed_row(r) for r in (overlay.get("feeds") or [])],
            "replacements": [dict(r) for r in (overlay.get("replacements") or [])],
            "disabled": list(overlay.get("disabled") or []),
        }
        _atomic_write(self.paths.overlay, OVERLAY_HEADER + "\n" + _dump(data))

    def write_wechat(self, feeds: list[dict]) -> None:
        data = {"feeds": [_feed_row(r) for r in feeds]}
        _atomic_write(self.paths.wechat, WECHAT_HEADER + "\n" + _dump(data))

    def _expand(self, url: str) -> str:
        return str(url).replace("{rsshub}", self.paths.rsshub_url.rstrip("/"))

    def _view(self, row: dict, *, origin: str, enabled: bool) -> FeedView:
        name = str(row.get("name") or "")
        url = str(row.get("url") or "")
        source = str(row.get("source") or "rss")
        weight = row.get("weight")
        return FeedView(
            name=name,
            url=url,
            url_expanded=self._expand(url),
            source=source,
            kind=str(row.get("kind") or "article"),
            weight=None if weight is None or weight == "" else float(weight),
            title_regex=str(row["title_regex"]) if row.get("title_regex") else None,
            title_exclude_regex=(
                str(row["title_exclude_regex"]) if row.get("title_exclude_regex") else None
            ),
            interval_minutes=(
                int(row["interval_minutes"]) if row.get("interval_minutes") else None
            ),
            origin=origin,
            enabled=enabled,
            collector=f"rss_{slugify(name)}" if name else "rss_unnamed",
            category=category_of(source),
        )

    def catalog(self) -> list[FeedView]:
        """含停用的内置源，方便在控制台里重新打开。"""
        builtin = self._builtin_feeds()
        overlay = self._overlay()
        wechat = self._wechat_feeds()
        disabled = {str(n) for n in overlay.get("disabled") or []}
        repl = {
            str(r["name"]): dict(r)
            for r in (overlay.get("replacements") or [])
            if r.get("name")
        }
        builtin_names = {str(r.get("name") or "") for r in builtin if r.get("name")}

        views: list[FeedView] = []
        for row in builtin:
            name = str(row.get("name") or "")
            if not name:
                continue
            merged = dict(row)
            extra = dict(repl.get(name) or {})
            extra.pop("name", None)
            merged.update(extra)
            merged["name"] = name
            views.append(
                self._view(merged, origin="builtin", enabled=name not in disabled)
            )

        for row in overlay.get("feeds") or []:
            name = str(row.get("name") or "")
            if not name or name in builtin_names:
                continue
            views.append(
                self._view(row, origin="overlay", enabled=name not in disabled)
            )

        for row in wechat:
            name = str(row.get("name") or "")
            if not name:
                continue
            views.append(
                self._view(row, origin="wechat", enabled=name not in disabled)
            )

        return views

    def get(self, name: str) -> FeedView | None:
        for v in self.catalog():
            if v.name == name:
                return v
        return None

    def merged_rows(self) -> list[dict]:
        overlay = self._overlay()
        rows = apply_overlay(self._builtin_feeds(), overlay)
        disabled = {str(n) for n in overlay.get("disabled") or []}
        for row in self._wechat_feeds():
            name = str(row.get("name") or "")
            if name and name not in disabled:
                rows.append(dict(row))
        return rows

    def _assert_name_free(self, name: str, *, ignore: str | None = None) -> None:
        for v in self.catalog():
            if v.name == name and v.name != ignore:
                raise FeedStoreError(f"已有同名订阅「{name}」")

    def add(self, row: dict, *, origin: str | None = None) -> FeedView:
        feed = _feed_row(row)
        name = feed["name"]
        if not name or not feed.get("url"):
            raise FeedStoreError("name 和 url 必填")
        self._assert_name_free(name)
        dest = origin or ("wechat" if feed["source"] == "wechat_mp" else "overlay")
        if dest == "wechat":
            feeds = self._wechat_feeds()
            feeds.append(feed)
            self.write_wechat(feeds)
        else:
            overlay = self._overlay()
            overlay.setdefault("feeds", []).append(feed)
            self.write_overlay(overlay)
        got = self.get(name)
        assert got is not None
        return got

    def patch(self, name: str, updates: dict) -> FeedView:
        view = self.get(name)
        if view is None:
            raise FeedStoreError(f"没有订阅「{name}」")

        if "enabled" in updates and updates["enabled"] is not None:
            self._set_enabled(name, bool(updates["enabled"]))

        fields = {k: updates[k] for k in EDITABLE if k in updates and updates[k] is not None}
        new_name = updates.get("name")
        if new_name is not None:
            new_name = str(new_name).strip()
            if new_name and new_name != name:
                if view.origin == "builtin":
                    raise FeedStoreError("内置源不能改名，请停用后再添加一条")
                self._assert_name_free(new_name, ignore=name)
                fields["name"] = new_name

        if fields:
            if view.origin == "builtin":
                self._patch_builtin(name, fields)
            elif view.origin == "overlay":
                self._patch_overlay_feed(name, fields)
            else:
                self._patch_wechat(name, fields)

        final_name = str(fields.get("name") or name)
        got = self.get(final_name)
        if got is None:
            raise FeedStoreError(f"更新后找不到「{final_name}」")
        return got

    def delete(self, name: str) -> dict:
        view = self.get(name)
        if view is None:
            raise FeedStoreError(f"没有订阅「{name}」")
        if view.origin == "builtin":
            overlay = self._overlay()
            disabled = list(overlay.get("disabled") or [])
            if name not in disabled:
                disabled.append(name)
            overlay["disabled"] = disabled
            self.write_overlay(overlay)
            return {"name": name, "action": "disabled", "origin": "builtin"}
        if view.origin == "overlay":
            overlay = self._overlay()
            overlay["feeds"] = [
                r for r in (overlay.get("feeds") or []) if str(r.get("name")) != name
            ]
            self.write_overlay(overlay)
            return {"name": name, "action": "removed", "origin": "overlay"}
        feeds = [r for r in self._wechat_feeds() if str(r.get("name")) != name]
        self.write_wechat(feeds)
        return {"name": name, "action": "removed", "origin": "wechat"}

    def _set_enabled(self, name: str, enabled: bool) -> None:
        overlay = self._overlay()
        disabled = [str(n) for n in (overlay.get("disabled") or [])]
        if enabled:
            overlay["disabled"] = [n for n in disabled if n != name]
        elif name not in disabled:
            disabled.append(name)
            overlay["disabled"] = disabled
        else:
            overlay["disabled"] = disabled
        self.write_overlay(overlay)

    def _patch_builtin(self, name: str, fields: dict) -> None:
        overlay = self._overlay()
        repl = list(overlay.get("replacements") or [])
        found = False
        for row in repl:
            if str(row.get("name")) == name:
                row.update({k: v for k, v in fields.items() if k != "name"})
                found = True
                break
        if not found:
            item = {"name": name}
            item.update({k: v for k, v in fields.items() if k != "name"})
            repl.append(item)
        overlay["replacements"] = repl
        self.write_overlay(overlay)

    def _patch_overlay_feed(self, name: str, fields: dict) -> None:
        overlay = self._overlay()
        feeds = []
        for row in overlay.get("feeds") or []:
            if str(row.get("name")) == name:
                row = dict(row)
                row.update(fields)
            feeds.append(row)
        overlay["feeds"] = feeds
        self.write_overlay(overlay)

    def _patch_wechat(self, name: str, fields: dict) -> None:
        feeds = []
        for row in self._wechat_feeds():
            if str(row.get("name")) == name:
                row = dict(row)
                row.update(fields)
            feeds.append(row)
        self.write_wechat(feeds)


def default_overlay() -> dict:
    return empty_overlay()
