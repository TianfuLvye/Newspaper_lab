"""WeWe RSS 客户端：列源、用文章链接订阅公众号。"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx


class WeweError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _unwrap_trpc(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    err = payload.get("error")
    if err:
        if isinstance(err, dict):
            raise WeweError(str(err.get("message") or err))
        raise WeweError(str(err))
    result = payload.get("result")
    if isinstance(result, dict):
        if result.get("error"):
            inner = result["error"]
            msg = inner.get("message") if isinstance(inner, dict) else inner
            raise WeweError(str(msg or "WeWe 返回错误"))
        data = result.get("data")
        if isinstance(data, dict) and "json" in data:
            return data["json"]
        return data
    if "json" in payload:
        return payload["json"]
    return payload


class WeweClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4000",
        *,
        auth_code: str | None = None,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_code = (
            auth_code if auth_code is not None else os.environ.get("WEWE_AUTH_CODE", "")
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def feed_url(self, mp_id: str, *, ext: str = "atom") -> str:
        return f"{self.base_url}/feeds/{mp_id}.{ext}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_code:
            headers["Authorization"] = self.auth_code
        return headers

    def _trpc(self, procedure: str, payload: Any, *, mutation: bool) -> Any:
        try:
            if mutation:
                r = self._client.post(
                    f"/trpc/{procedure}",
                    json={"json": payload},
                    headers=self._headers(),
                )
            else:
                encoded = quote(
                    json.dumps({"json": payload}, ensure_ascii=False),
                    safe="",
                )
                r = self._client.get(
                    f"/trpc/{procedure}?input={encoded}",
                    headers=self._headers(),
                )
        except httpx.RequestError as e:
            raise WeweError(
                f"连不上 WeWe RSS（{self.base_url}）。先 docker compose "
                f"-f docker-compose.yml -f docker-compose.wewe-rss.yml up -d"
            ) from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except (ValueError, json.JSONDecodeError):
                raise WeweError(
                    f"WeWe HTTP {r.status_code}: {r.text[:300]}",
                    status=r.status_code,
                ) from None
            _unwrap_trpc(body)
            raise WeweError(f"WeWe HTTP {r.status_code}", status=r.status_code)
        try:
            return _unwrap_trpc(r.json())
        except (ValueError, json.JSONDecodeError, TypeError, KeyError) as e:
            raise WeweError(f"WeWe 响应无法解析: {e}") from e

    def get_mp_info(self, article_url: str) -> dict:
        """platform.getMpInfo → {id, name, cover, intro, updateTime}。"""
        data = self._trpc(
            "platform.getMpInfo",
            {"wxsLink": article_url},
            mutation=True,
        )
        item = None
        if isinstance(data, list) and data:
            item = data[0]
        elif isinstance(data, dict):
            item = data
        if not item:
            raise WeweError("WeWe 没认出这个公众号，检查是不是文章分享链接")
        mp_id = item.get("id")
        name = item.get("name") or item.get("mpName")
        if not mp_id or not name:
            raise WeweError("WeWe 返回里没有公众号 id/名称")
        return {
            "id": str(mp_id),
            "name": str(name),
            "cover": str(item.get("cover") or item.get("mpCover") or ""),
            "intro": str(item.get("intro") or item.get("mpIntro") or ""),
            "updateTime": int(item.get("updateTime") or 0),
        }

    def add_feed(self, mp: dict) -> dict:
        payload = {
            "id": mp["id"],
            "mpName": mp["name"],
            "mpCover": mp.get("cover") or "",
            "mpIntro": mp.get("intro") or "",
            "updateTime": int(mp.get("updateTime") or 0),
            "status": 1,
        }
        return self._trpc("feed.add", payload, mutation=True) or payload

    def list_feeds(self) -> list[dict]:
        """优先 GET /feeds/（不必 AUTH_CODE），失败再走 tRPC feed.list。"""
        try:
            r = self._client.get("/feeds/", timeout=8.0)
        except httpx.RequestError as e:
            raise WeweError(
                f"连不上 WeWe RSS（{self.base_url}）。确认容器已启动。"
            ) from e
        listed: list[dict] | None = None
        if r.status_code < 400:
            try:
                data = r.json()
            except (ValueError, json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, list):
                listed = [_normalize_listed(x) for x in data if isinstance(x, dict)]
        if listed is not None:
            return listed
        data = self._trpc("feed.list", {"limit": 1000}, mutation=False)
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [_normalize_listed(x) for x in items if isinstance(x, dict)]


def _normalize_listed(item: dict) -> dict:
    mp_id = str(item.get("id") or "")
    name = str(item.get("name") or item.get("mpName") or mp_id)
    return {
        "id": mp_id,
        "name": name,
        "intro": str(item.get("intro") or item.get("mpIntro") or ""),
        "cover": str(item.get("cover") or item.get("mpCover") or ""),
    }
