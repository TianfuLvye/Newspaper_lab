"""LLM 环境变量。文本任务用 Flash，识图用 Visual。"""
from __future__ import annotations

import os
import re

import httpx

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def llm_api_key() -> str | None:
    key = os.environ.get("FISHNET_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return key or None


def llm_base_url() -> str:
    return (
        os.environ.get("FISHNET_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def _first_model(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return DEFAULT_MODEL


def llm_flash_model() -> str:
    """评委、综述、口播改稿等纯文本。"""
    return _first_model("FISHNET_LLM_FLASH_MODEL", "FISHNET_LLM_MODEL")


def llm_visual_model() -> str:
    """配图挑选等需要识图能力的调用。"""
    return _first_model("FISHNET_LLM_VISUAL_MODEL", "FISHNET_LLM_MODEL")


def strip_fences(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json|markdown)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


def chat_completions(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: float = 120.0,
    thinking: bool | None = None,
    max_tokens: int | None = None,
) -> str:
    """Flash 文本补全。thinking 为 True/False 时写入 DeepSeek 思考开关；None 则不传该字段。"""
    key = llm_api_key()
    if not key:
        raise RuntimeError("FISHNET_LLM_API_KEY missing")
    payload: dict = {
        "model": llm_flash_model(),
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{llm_base_url()}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
        response.raise_for_status()
        body = response.json()
    message = body["choices"][0]["message"]
    content = strip_fences(message.get("content") or "")
    if not content:
        raise ValueError("empty LLM content")
    return content
