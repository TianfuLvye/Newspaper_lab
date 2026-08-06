"""关键词 DSL —— Lab 2 标准答案。

语义(借鉴 TrendRadar 的 must/any/exclude,但把「版面归属」加进来了,
这是它没有而你需要的):

    must    : 全部命中才算匹配(AND)
    any     : 命中任意一个即可(OR);must 为空时 any 必须命中
    exclude : 命中任意一个立即淘汰(优先级最高)
    weight  : 命中后给 Item 的加权
    sections: 命中后这条内容进哪个版面

匹配在 title + summary + content 上做,但**标题命中权重更高**——
标题里出现「宁德时代」和正文里顺带提一句,价值完全不同。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class KeywordGroup:
    name: str
    must: list[str] = field(default_factory=list)
    any: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    weight: float = 1.0
    sections: list[str] = field(default_factory=list)
    # 别名:解决「宇树 / Unitree / 四足机器人」这类同义问题
    aliases: dict[str, list[str]] = field(default_factory=dict)

    def _expand(self, words: list[str]) -> list[list[str]]:
        """每个词展开成 [原词] + 别名,组内是 OR 关系。"""
        return [[w, *self.aliases.get(w, [])] for w in words]


@dataclass
class MatchResult:
    matched: bool
    group: str | None = None
    weight: float = 0.0
    sections: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)
    in_title: bool = False


def _find(variants: list[str], text: str) -> str | None:
    for v in variants:
        if not v:
            continue
        # 纯 ASCII 词加词边界,避免 "AI" 命中 "SAID";中文不需要边界
        if re.fullmatch(r"[\x00-\x7f]+", v):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(v)}(?![A-Za-z0-9])", text, re.I):
                return v
        elif v.lower() in text:
            return v
    return None


class KeywordEngine:
    def __init__(self, groups: list[KeywordGroup]):
        self.groups = groups

    @classmethod
    def from_yaml(cls, path: str | Path) -> "KeywordEngine":
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls([KeywordGroup(**g) for g in cfg.get("groups", [])])

    def match_one(self, g: KeywordGroup, title: str, body: str) -> MatchResult:
        title_l, full = title.lower(), (title + "\n" + body).lower()

        # 1) exclude 优先级最高,一票否决
        for variants in g._expand(g.exclude):
            if _find(variants, full):
                return MatchResult(False)

        hits: list[str] = []
        # 2) must 全中
        for variants in g._expand(g.must):
            hit = _find(variants, full)
            if not hit:
                return MatchResult(False)
            hits.append(hit)
        # 3) any 至少一中(must 为空时必须有 any 命中,否则该组等于匹配一切)
        any_expanded = g._expand(g.any)
        if any_expanded:
            any_hits = [h for vs in any_expanded if (h := _find(vs, full))]
            if not any_hits and not g.must:
                return MatchResult(False)
            if not any_hits and g.must:
                pass  # must 已全中,any 只是加分项
            hits += any_hits
        elif not g.must:
            return MatchResult(False)  # 空组不匹配任何东西

        in_title = any(_find([h], title_l) for h in hits)
        w = g.weight * (1.5 if in_title else 1.0)
        return MatchResult(True, g.name, w, list(g.sections), hits, in_title)

    def match(self, title: str, body: str = "") -> list[MatchResult]:
        out = [r for g in self.groups if (r := self.match_one(g, title, body)).matched]
        return sorted(out, key=lambda r: -r.weight)

    def score(self, title: str, body: str = "") -> float:
        """关键词得分 S_kw。多组命中取加和后做饱和,避免堆词刷分。"""
        rs = self.match(title, body)
        if not rs:
            return 0.0
        total = sum(r.weight for r in rs)
        return total / (total + 2.0)   # 归一到 (0,1),边际递减
