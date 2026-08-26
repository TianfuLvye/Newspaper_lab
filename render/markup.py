"""报纸正文里的轻量 Markdown:只处理加粗/斜体,不当纯文本把 ** 印出去。"""
from __future__ import annotations

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)")


def strip_inline_md(text: str) -> str:
    s = _BOLD.sub(r"\1", text or "")
    return _ITALIC.sub(r"\1", s)


def inline_md_to_html(escaped: str) -> str:
    """`escaped` 必须已经 html.escape 过,星号本身不会被转义。"""
    s = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", s)
