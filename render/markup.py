"""报纸正文里的轻量 Markdown:加粗/斜体/管道表格,不当纯文本把 ** 印出去。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)")
_TABLE_ALIGN = re.compile(r"^:?-{2,}:?$")


def strip_inline_md(text: str) -> str:
    s = _BOLD.sub(r"\1", text or "")
    return _ITALIC.sub(r"\1", s)


def inline_md_to_html(escaped: str) -> str:
    """`escaped` 必须已经 html.escape 过,星号本身不会被转义。"""
    s = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", s)


@dataclass
class MdTable:
    header: list[str]
    aligns: list[str]  # "l" / "r" / "c",来自 |---|---:|:-:| 行
    rows: list[list[str]] = field(default_factory=list)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_table(para: str) -> MdTable | None:
    """一「段」里每行都是 | ... | 且第二行是对齐分隔线,才算表格。"""
    lines = [ln for ln in para.split("\n") if ln.strip()]
    if len(lines) < 2 or not all(ln.strip().startswith("|") for ln in lines):
        return None
    sep = _split_row(lines[1])
    if not sep or not all(_TABLE_ALIGN.match(c.replace(" ", "")) for c in sep):
        return None
    aligns = []
    for c in sep:
        c = c.replace(" ", "")
        aligns.append("c" if c.startswith(":") and c.endswith(":") else ("r" if c.endswith(":") else "l"))
    header = _split_row(lines[0])
    rows = [_split_row(ln) for ln in lines[2:]]
    return MdTable(header=header, aligns=aligns, rows=rows)


def split_md_segments(text: str) -> list[tuple[str, object]]:
    """把正文切成 ("text", str) / ("table", MdTable) 段,顺序不变。

    表格段在渲染层走真正的表格;量格子时仍按原始管道文本估高,
    估出来的高度只多不少,给渲染留出余量。
    """
    out: list[tuple[str, object]] = []
    for para in (text or "").split("\n\n"):
        para = para.strip("\n")
        if not para.strip():
            continue
        tbl = _parse_table(para)
        if tbl is not None:
            out.append(("table", tbl))
        else:
            if out and out[-1][0] == "text":
                out[-1] = ("text", f"{out[-1][1]}\n\n{para}")
            else:
                out.append(("text", para))
    return out


def has_md_table(text: str) -> bool:
    return any(kind == "table" for kind, _ in split_md_segments(text))
