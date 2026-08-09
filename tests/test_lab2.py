"""Lab 2 验收:关键词 DSL + keywords.yaml 覆盖面。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.schema import Item, Kind, Source
from pipeline.keyword import KeywordEngine, KeywordGroup

PASS = FAIL = 0
YAML = ROOT / "config" / "keywords.yaml"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


print("\n[Lab 2] keywords.yaml 覆盖面")
eng = KeywordEngine.from_yaml(YAML)
names = [g.name for g in eng.groups]
check("至少 5 个关键词组", len(eng.groups) >= 5, str(names))
sections = {s for g in eng.groups for s in g.sections}
check("覆盖 finance", "finance" in sections, str(sections))
check("覆盖 tech/AI", "tech" in sections, str(sections))
check("覆盖 policy/政治经济", "policy" in sections, str(sections))
check("每组都有 exclude", all(g.exclude for g in eng.groups))


print("\n[Lab 2] must / any / exclude / weight")
check("must+any 命中", eng.match("宁德时代发布三季度财报")[0].group.startswith("自选股"))
check("must 缺失不命中", not any("自选股" in (r.group or "") for r in eng.match("比亚迪财报")))
check("exclude 一票否决", not eng.match("宁德时代财报 股吧热议"))
check("别名生效", any(r.group == "AI 进展" for r in eng.match("人工智能新进展")))
check("英文词边界", not any(r.group == "AI 进展" for r in eng.match("HE SAID SOMETHING")))
check(
    "标题命中权重更高",
    eng.match("大模型突破")[0].weight > eng.match("其他事", "正文提到大模型")[0].weight,
)
check("空组不匹配", KeywordEngine([KeywordGroup(name="x")]).match("任意") == [])
check("score 饱和在 (0,1)", 0 < eng.score("宁德时代财报") < 1)


print("\n[Lab 2] annotate / filter_matched(加工层接口)")
items = [
    Item(Source.NEWS, Kind.HOTLIST, "宁德时代获大额订单", "https://a/1", collector="t"),
    Item(Source.NEWS, Kind.HOTLIST, "今天天气不错", "https://a/2", collector="t"),
    Item(Source.ZHIHU, Kind.HOTLIST, "OpenAI 发布推理模型", "https://a/3", collector="t"),
]
ann = eng.annotate(items)
check("annotate 条数不变", len(ann) == 3)
kept = eng.filter_matched(items)
check("无关条目被滤掉", len(kept) == 2, str([(i.title, m.group) for i, m in kept]))
check("保留条目带 MatchResult", all(m.matched for _, m in kept))


print(f"\n{'=' * 60}\n  PASSED {PASS}   FAILED {FAIL}\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
