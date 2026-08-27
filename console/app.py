"""订阅源控制台 HTTP API + 静态页。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from collectors.rss_generic import RSSCollector
from console.detect import DetectError, FeedDraft, detect_input
from console.wewe import WeweClient, WeweError
from console.yaml_io import FeedPaths, FeedStore, FeedStoreError
from core.base import run_collector
from core.settings import load_env_file, load_settings
from core.store import Store

STATIC_DIR = Path(__file__).resolve().parent / "static"


class DetectIn(BaseModel):
    url: str


class FeedIn(BaseModel):
    name: str
    url: str = ""
    source: str = "rss"
    kind: str = "article"
    weight: float = 2.0
    title_regex: str | None = None
    interval_minutes: int | None = None
    type: str | None = None
    needs_wewe: bool = False
    wechat_article_url: str | None = None
    mp_id: str | None = None


class FeedPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    source: str | None = None
    kind: str | None = None
    weight: float | None = None
    title_regex: str | None = Field(default=None)
    interval_minutes: int | None = None
    enabled: bool | None = None
    clear_title_regex: bool = False


class WeweImportIn(BaseModel):
    ids: list[str] | None = None


def _http(err: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(err))


def _enrich_wechat_draft(draft: FeedDraft, wewe: WeweClient) -> FeedDraft:
    if not draft.needs_wewe or not draft.wechat_article_url:
        return draft
    try:
        mp = wewe.get_mp_info(draft.wechat_article_url)
    except WeweError as e:
        draft.warning = str(e)
        return draft
    draft.name = mp["name"]
    draft.mp_id = mp["id"]
    draft.url = wewe.feed_url(mp["id"])
    return draft


def _subscribe_wechat(body: FeedIn, wewe: WeweClient) -> dict:
    article = body.wechat_article_url
    mp_id = body.mp_id
    name = body.name
    url = body.url
    if article:
        mp = wewe.get_mp_info(article)
        wewe.add_feed(mp)
        mp_id = mp["id"]
        name = body.name if body.name and body.name != "微信公众号" else mp["name"]
        url = wewe.feed_url(mp_id)
    elif mp_id and not url:
        url = wewe.feed_url(mp_id)
    if not url:
        raise WeweError("缺少公众号 feed URL。请贴文章链接，或 WeWe 的 /feeds/MP_WXS_….atom")
    return {
        "name": name,
        "url": url,
        "source": "wechat_mp",
        "kind": body.kind or "article",
        "weight": body.weight,
        "title_regex": body.title_regex,
        "interval_minutes": body.interval_minutes,
    }


def create_app(
    *,
    paths: FeedPaths | None = None,
    db_path: Path | None = None,
    wewe: WeweClient | None = None,
) -> FastAPI:
    load_env_file()
    settings = load_settings()
    store = FeedStore(paths or FeedPaths())
    database = Path(db_path) if db_path is not None else settings.db_path
    client = wewe or WeweClient(settings.wewe_url)

    app = FastAPI(title="Fishnet 订阅台", docs_url=None, redoc_url=None)

    def attach_runs(items: list[dict]) -> list[dict]:
        runs: dict[str, dict] = {}
        if database.exists():
            db = Store(database)
            try:
                runs = db.latest_runs_by_collector()
            finally:
                db.close()
        for item in items:
            run = runs.get(item["collector"]) or {}
            item["last_run"] = {
                "status": run.get("status"),
                "started_at": run.get("started_at"),
                "new_count": run.get("new_count"),
                "item_count": run.get("item_count"),
                "error": run.get("error"),
            }
        return items

    @app.get("/api/meta")
    def meta():
        return {
            "rsshub_url": store.paths.rsshub_url,
            "wewe_url": client.base_url,
            "auth_configured": bool(os.environ.get("WEWE_AUTH_CODE") or client.auth_code),
            "hint": "新源会立即出现在下一次 collect 里。若正在跑 serve，需重启后才会给新源挂定时轮询。",
        }

    @app.get("/api/feeds")
    def list_feeds():
        return {"feeds": attach_runs([v.to_dict() for v in store.catalog()])}

    @app.post("/api/feeds/detect")
    def detect(body: DetectIn):
        try:
            draft = detect_input(body.url, wewe_base=client.base_url)
        except DetectError as e:
            raise _http(e) from e
        draft = _enrich_wechat_draft(draft, client)
        return draft.to_dict()

    @app.post("/api/feeds")
    def add_feed(body: FeedIn):
        try:
            if body.needs_wewe or body.wechat_article_url or (
                body.source == "wechat_mp" and not body.url
            ):
                row = _subscribe_wechat(body, client)
            else:
                row = {
                    "name": body.name,
                    "url": body.url,
                    "source": body.source,
                    "kind": body.kind,
                    "weight": body.weight,
                    "title_regex": body.title_regex,
                    "interval_minutes": body.interval_minutes,
                }
            origin = "wechat" if row["source"] == "wechat_mp" else "overlay"
            view = store.add(row, origin=origin)
        except (FeedStoreError, DetectError) as e:
            raise _http(e) from e
        except WeweError as e:
            raise _http(e, status=502) from e
        return view.to_dict()

    @app.patch("/api/feeds/{name}")
    def patch_feed(name: str, body: FeedPatch):
        updates = body.model_dump(exclude_unset=True)
        updates.pop("clear_title_regex", None)
        if body.clear_title_regex:
            overlay_patch = dict(updates)
            # title_regex 空字符串表示清掉
            overlay_patch["title_regex"] = ""
            updates = overlay_patch
        try:
            view = store.patch(name, updates)
        except FeedStoreError as e:
            status = 404 if str(e).startswith("没有订阅") else 400
            raise _http(e, status=status) from e
        return view.to_dict()

    @app.delete("/api/feeds/{name}")
    def delete_feed(name: str):
        try:
            return store.delete(name)
        except FeedStoreError as e:
            raise _http(e, status=404) from e

    @app.post("/api/feeds/{name}/collect")
    def collect_feed(name: str):
        view = store.get(name)
        if view is None:
            raise HTTPException(status_code=404, detail=f"没有订阅「{name}」")
        if not view.enabled:
            raise HTTPException(status_code=400, detail="已停用，无法采集")
        row = {
            "name": view.name,
            "url": view.url_expanded,
            "source": view.source,
            "kind": view.kind,
        }
        if view.weight is not None:
            row["weight"] = view.weight
        if view.title_regex:
            row["title_regex"] = view.title_regex
        if view.interval_minutes:
            row["interval_minutes"] = view.interval_minutes
        collector = RSSCollector(row)
        db = Store(database)
        try:
            new, dup = run_collector(collector, db)
        finally:
            db.close()
        return {"collector": collector.name, "new": new, "dup": dup}

    @app.get("/api/wewe/feeds")
    def wewe_feeds():
        try:
            items = client.list_feeds()
        except WeweError as e:
            raise _http(e, status=502) from e
        existing = {v.url_expanded for v in store.catalog() if v.origin == "wechat"}
        existing_ids = set()
        for v in store.catalog():
            if v.origin != "wechat":
                continue
            for part in v.url.split("/"):
                if part.startswith("MP_WXS_"):
                    existing_ids.add(part.split(".")[0])
        out = []
        for item in items:
            fid = item["id"]
            url = client.feed_url(fid)
            out.append(
                {
                    **item,
                    "url": url,
                    "in_fishnet": fid in existing_ids or url in existing,
                }
            )
        return {"feeds": out}

    @app.post("/api/wewe/import")
    def wewe_import(body: WeweImportIn):
        try:
            items = client.list_feeds()
        except WeweError as e:
            raise _http(e, status=502) from e
        wanted = set(body.ids) if body.ids else None
        added = []
        skipped = []
        for item in items:
            if wanted is not None and item["id"] not in wanted:
                continue
            row = {
                "name": item["name"],
                "url": client.feed_url(item["id"]),
                "source": "wechat_mp",
                "kind": "article",
                "weight": 2.0,
            }
            try:
                added.append(store.add(row, origin="wechat").to_dict())
            except FeedStoreError as e:
                skipped.append({"name": item["name"], "reason": str(e)})
        return {"added": added, "skipped": skipped}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
