"""批判性思考评分 —— Lab 7 S_llm。

工业推荐的两阶段:粗排(便宜信号) → 精排(LLM 评委)。
评委很贵,所以:
  - 每期最多 coarse_k 次(默认 150)
  - 已有 llm_summary 的条目不重复打
  - 没配 API Key 时走启发式,保证离线可测、出报不中断

启发式不是「假 LLM」,它是按同一份 rubric 抽可检验特征:
数据/多种解释/标明边界 → 加分;标题党/营销/纯播报 → 减分。
接上 API 后,同一接口换成 JSON 评委即可。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.llm import llm_api_key, llm_base_url, llm_flash_model

CRITICAL_RUBRIC = """
你是一位严格的内容评审。请对下面这篇文章在「激发批判性思考」维度上打分(0-10)。

高分特征:
- 提出了与主流叙事相左的观点, 并给出可检验的论据
- 揭示了一个被普遍忽略的前提假设或概念混淆
- 呈现了同一事实的多种解释, 而非单一结论
- 包含具体数据、原始文献或一手观察, 而非二手转述
- 作者明确标注了自己论证的边界与不确定性

低分特征:
- 情绪煽动、结论先行、诉诸群体认同
- 营销软文、标题党、伪科普
- 纯资讯播报, 无分析增量
- 观点正确但论证空洞, 只是重复常识

不要因为文章长就给高分。只输出 JSON:
{"score": <0-10>, "reason": "<40字以内>", "angle": "<这篇挑战了什么假设>"}
"""


@dataclass
class CriticResult:
    score01: float          # 写入总分的 0-1
    raw: float              # 0-10
    reason: str
    angle: str
    source: str             # heuristic / llm / cached

    def as_json(self) -> str:
        return json.dumps(
            {
                "score": self.raw,
                "reason": self.reason,
                "angle": self.angle,
                "source": self.source,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, blob: str) -> "CriticResult | None":
        try:
            d = json.loads(blob)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(d, dict) or "score" not in d:
            return None
        raw = float(d["score"])
        return cls(
            score01=max(0.0, min(raw / 10.0, 1.0)),
            raw=raw,
            reason=str(d.get("reason") or ""),
            angle=str(d.get("angle") or ""),
            source=str(d.get("source") or "cached"),
        )


_HIGH = ("然而", "但是", "未必", "假设", "前提", "边界", "相反", "另一种",
         "并不意味着", "值得怀疑", "因果", "混淆", "可检验", "不确定")
_DATA = re.compile(r"\d+(\.\d+)?%?|同比|环比|样本|实验|文献|论文")
_LOW = ("震惊", "速看", "必看", "家人们", "转发收藏", "不转不是", "性价比之王",
        "抓紧入手", "日入", "割韭菜", "涨停板", "内幕")
_NEWSY = ("据悉", "消息称", "最新进展", "官方通报", "发布会")


def heuristic_critic(title: str, body: str) -> CriticResult:
    """按 rubric 抽特征。分数刻意保守,避免启发式支配总分。"""
    text = f"{title}\n{body or ''}"
    s = 4.0  # 中性偏下,纯播报不该混进「今日一问」
    high_hits = [w for w in _HIGH if w in text]
    s += min(2.5, 0.45 * len(high_hits))
    if _DATA.search(text):
        s += 1.2
    low_hits = [w for w in _LOW if w in text]
    s -= min(3.0, 1.2 * len(low_hits))
    news_hits = [w for w in _NEWSY if w in text]
    if news_hits and not high_hits:
        s -= 1.0
    n = len(body or "")
    if n < 80:
        s -= 1.5
    elif n > 8000:
        s -= 0.4  # 明确不因长而加分,略罚裹脚布
    s = max(0.0, min(10.0, s))
    if high_hits:
        angle = f"触及:{high_hits[0]}"
        reason = "有反例/前提/边界,启发式加分"
    elif low_hits:
        angle = "像营销或标题党"
        reason = "命中低分特征"
    else:
        angle = "未检测出明确假设挑战"
        reason = "中性资讯"
    return CriticResult(score01=s / 10.0, raw=s, reason=reason, angle=angle,
                        source="heuristic")


class Critic:
    """可替换评委。call_count 用来验收「每期 ≤150」。"""

    def __init__(self, *, prefer_llm: bool | None = None, timeout: float = 20.0):
        self.timeout = timeout
        self.call_count = 0
        key = llm_api_key()
        self.base = llm_base_url()
        self.model = llm_flash_model()
        self.api_key = key
        if prefer_llm is None:
            self.prefer_llm = bool(key)
        else:
            self.prefer_llm = prefer_llm and bool(key)

    def judge(self, title: str, body: str, *, cached: str | None = None) -> CriticResult:
        got = CriticResult.from_json(cached or "")
        if got is not None:
            got.source = "cached"
            return got
        self.call_count += 1
        if self.prefer_llm:
            try:
                return self._llm(title, body)
            except Exception:
                return heuristic_critic(title, body)
        return heuristic_critic(title, body)

    def _llm(self, title: str, body: str) -> CriticResult:
        import httpx

        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": CRITICAL_RUBRIC.strip()},
                {
                    "role": "user",
                    "content": f"标题: {title}\n\n正文:\n{(body or '')[:4000]}",
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.base}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        d = json.loads(content)
        raw = float(d["score"])
        return CriticResult(
            score01=max(0.0, min(raw / 10.0, 1.0)),
            raw=raw,
            reason=str(d.get("reason") or "")[:40],
            angle=str(d.get("angle") or ""),
            source="llm",
        )
