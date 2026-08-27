"""LLM 环境变量。文本任务用 Flash，识图用 Visual。"""
from __future__ import annotations

import os

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
