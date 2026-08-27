"""火山引擎录音文件识别（flash）。不用 Whisper / torch。"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

import httpx

FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
DEFAULT_RESOURCE_ID = "volc.seedasr.auc"


def stt_api_key() -> str | None:
    key = (os.environ.get("STT_API_KEY") or "").strip()
    return key or None


def transcribe_audio(
    audio_path: Path,
    *,
    api_key: str | None = None,
    resource_id: str | None = None,
    timeout: float = 600.0,
) -> str:
    key = (api_key or stt_api_key() or "").strip()
    if not key:
        raise RuntimeError("STT_API_KEY missing")
    rid = (resource_id or os.environ.get("STT_RESOURCE_ID") or DEFAULT_RESOURCE_ID).strip()
    audio_path = Path(audio_path)
    fmt = audio_path.suffix.lstrip(".").lower() or "m4a"
    payload = {
        "user": {"uid": key},
        "audio": {
            "format": fmt,
            "data": base64.b64encode(audio_path.read_bytes()).decode("utf-8"),
        },
        "request": {
            "model_name": "bigmodel",
            "show_utterances": True,
            "enable_itn": True,
        },
    }
    headers = {
        "X-Api-Key": key,
        "Content-Type": "application/json",
        "X-Api-Resource-Id": rid,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(FLASH_URL, headers=headers, json=payload)
    status_code = response.headers.get("X-Api-Status-Code", "")
    status_msg = response.headers.get("X-Api-Message", "")
    if response.status_code != 200:
        detail = status_msg or response.text[:300]
        raise RuntimeError(f"Volcengine HTTP {response.status_code}: [{status_code}] {detail}")
    if status_code == "20000003":
        return ""
    if status_code != "20000000":
        raise RuntimeError(f"Volcengine recognize failed: [{status_code}] {status_msg}")
    data = response.json()
    result = data.get("result") or {}
    utterances = result.get("utterances") or []
    text = "\n".join(item.get("text", "") for item in utterances if item.get("text")).strip()
    if not text:
        text = (result.get("text") or "").strip()
    if not text:
        raise RuntimeError("Volcengine returned empty transcript")
    return text
