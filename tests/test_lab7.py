"""Lab 7 验收:黄金集向量化、两阶段召回、事件折叠、A/B、反馈闭环。"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.schema import Item, Kind, Source
from core.store import Store
from main import build_parser, main
from pipeline.critic import Critic, heuristic_critic
from pipeline.dedup import cluster_by_embedding, fold_events, hamming, simhash
from pipeline.embed import TfidfEmbedder
from pipeline.golden import fit_taste, load_golden
from pipeline.golden_seed import SEED
from pipeline.rank import heat_only_order, is_rank_candidate, rank_items
from pipeline.edition import produce_edition

PASS = FAIL = 0
DOC = ROOT / "docs" / "lab-07-ranking.md"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


print("\n[Lab 7] 黄金集 ≥50 并完成向量化")
docs = load_golden()
check("seed ≥50", len(docs) >= 50, str(len(docs)))
check("SEED 与 load 一致下限", len(SEED) >= 50)
fitted = fit_taste(docs)
check("拟合出多簇", fitted.profile.k >= 2, f"k={fitted.profile.k}")
check("向量维数 > 0", fitted.embedder.dim >= 8, str(fitted.embedder.dim))
vecs = fitted.embedder.transform([d.text for d in docs])
check("每人一篇向量", len(vecs) == len(docs))
# 同簇应比跨簇更近:拿 ai vs finance 的质心近似
ai = [i for i, d in enumerate(docs) if d.cluster == "ai"][:5]
fn = [i for i, d in enumerate(docs) if d.cluster == "finance"][:5]
if ai and fn:
    import numpy as np

    intra = float((vecs[ai] @ vecs[ai].T).mean())
    inter = float((vecs[ai] @ vecs[fn].T).mean())
    check("同簇余弦 > 跨簇", intra > inter, f"intra={intra:.3f} inter={inter:.3f}")


print("\n[Lab 7] 两阶段召回,LLM 调用量 ≤150")
critic = Critic(prefer_llm=False)
now = datetime.now(timezone.utc)
cands: list[Item] = []
for i in range(180):
    body = (
        "然而这个前提未必成立。数据上同比下降 12%,另一种解释是口径变了。"
        if i % 17 == 0
        else f"据悉今日发布会顺利召开,最新进展请关注。条目{i}"
    )
    cands.append(
        Item(
            Source.ZHIHU if i % 3 else Source.NEWS,
            Kind.ARTICLE if i % 2 else Kind.HOTLIST,
            f"{'宁德时代订单可检验吗' if i % 17 == 0 else '热点快讯'}{i}",
            f"https://example.com/lab7/{i}",
            content=body * 3,
            summary=body,
            heat=1e6 - i * 1000,
            rank=i + 1,
            collector="test",
            fetched_at=now,
        )
    )
# 改写转载,用来测 L3 折叠
cands.append(
    Item(
        Source.NEWS, Kind.ARTICLE,
        "宁德时代发布新一代麒麟电池 能量密度大幅提升",
        "https://a.example/catl-1",
        content="宁德时代发布新一代麒麟电池,能量密度提升,但交付节奏仍需看合同条款。",
        heat=9e5, collector="wire1", fetched_at=now, score=0.4,
    )
)
cands.append(
    Item(
        Source.NEWS, Kind.ARTICLE,
        "宁德时代今日发布新一代麒麟电池 官方称能量密度提升明显",
        "https://b.example/catl-2",
        content="官方称宁德时代新一代麒麟电池能量密度提升明显。另一种解释是口径变化。",
        heat=8e5, collector="wire2", fetched_at=now, score=0.3,
    )
)
result = rank_items(cands, kind="am", taste=fitted, critic=critic, write_store=None)
check("热榜被踢出打分池", result.n_candidates < 180, str(result.n_candidates))
check("粗排截到 ≤150", result.n_coarse <= 150, str(result.n_coarse))
check("评委调用 ≤150", result.n_llm <= 150, str(result.n_llm))
check("头版非空", len(result.headline) >= 1)
check(
    "个性化版没有热榜标题",
    all(ri.item.kind != Kind.HOTLIST for ri in result.headline + result.deepread + result.critical),
)
vid = Item(Source.BILIBILI, Kind.VIDEO, "塔菲视频", "https://bilibili.com/v/1", content="相关推荐墙" * 20)
act = Item(Source.ZHIHU, Kind.ARTICLE, "Thoughts Memo赞同了回答: x", "https://zhihu.com/a/1", content="正经回答" * 40, collector="rss_thoughts_memo_动态")
check("视频不进打分", is_rank_candidate(vid) is False)
check("赞同动态不进打分", is_rank_candidate(act) is False)

print("\n[Lab 7] 当天知乎日报钉进深度,不靠把 weight 调很大")
import numpy as np
from pipeline.rank import DEEPREAD_N, RankedItem, ensure_todays_zhihu_daily
from pipeline.score import ScoreBreakdown

_cst = timezone(timedelta(hours=8))
pin_now = datetime(2026, 8, 27, 14, 0, tzinfo=_cst)
daily_pin = Item(
    Source.ZHIHU,
    Kind.ARTICLE,
    "泥石流；国自然；巫师3…",
    "https://zhuanlan.zhihu.com/p/daily-pin",
    content="嘿，这里是知乎早报！" * 20,
    collector="rss_知乎日报_早报",
    published_at=pin_now,
)
others = [
    Item(
        Source.NEWS,
        Kind.ARTICLE,
        f"占位稿{i}",
        f"https://example.com/slot-{i}",
        content="长正文足够过门槛。" * 30,
        fetched_at=pin_now,
    )
    for i in range(8)
]
_bd = ScoreBreakdown(0.4, {"sim": 0.4})
deep = [RankedItem(it, _bd, np.zeros(4)) for it in others]
used_pin = {it.content_hash for it in others}
_h, _d, _c = ensure_todays_zhihu_daily(
    [daily_pin, *others], [], deep, [], [], used_pin, pin_now
)
check("日报钉在深度第一", _d[0].item.content_hash == daily_pin.content_hash)
check("深度仍不超过配额", len(_d) == DEEPREAD_N)
check(
    "挤掉最后一篇占位",
    others[-1].content_hash not in {x.item.content_hash for x in _d},
)
_h2, _d2, _c2 = ensure_todays_zhihu_daily(
    [daily_pin], [_d[0]], [], [], [], set(), pin_now
)
check("已上版则不重复钉", _h2[0].item.content_hash == daily_pin.content_hash and not _d2)
old_daily = Item(
    Source.ZHIHU,
    Kind.ARTICLE,
    "昨天的早报",
    "https://zhuanlan.zhihu.com/p/daily-old",
    content="嘿，这里是知乎早报！" * 20,
    collector="rss_知乎日报_早报",
    published_at=pin_now - timedelta(days=1),
)
_h3, _d3, _c3 = ensure_todays_zhihu_daily([old_daily], [], [], [], [], set(), pin_now)
check("昨天的日报不钉到今天", not _d3)


print("\n[Lab 7] 事件聚类:改写稿被折叠(SimHash 抓不到,L3 要抓到)")
t1 = "宁德时代发布新一代麒麟电池 能量密度大幅提升"
t3 = "宁德时代今日发布新一代麒麟电池 官方称能量密度提升明显"
check("L2 仍抓不到改写", hamming(simhash(t1), simhash(t3)) > 3)
emb = TfidfEmbedder(dim=32)
emb.fit([t1, t3, "上海暴雨地铁停运无关新闻"] + [d.text for d in docs])
v = emb.transform([t1, t3, "上海暴雨地铁停运无关新闻"])
labels = cluster_by_embedding(v, threshold=0.45)
check("L3 把改写稿聚在一起", labels[0] == labels[1], str(labels))
check("L3 不误伤无关", labels[0] != labels[2], str(labels))

pair = [cands[-2], cands[-1]]
pv = emb.transform([x.text_for_embedding for x in pair])
kept, folded = fold_events(pair, pv, cosine_threshold=0.45)
check("fold_events 只留 1 条主稿", len(kept) == 1, f"kept={len(kept)} folded={folded}")


print("\n[Lab 7] A/B:纯热度 vs 打分,顺序可以不同")
heat = heat_only_order(cands, n=10)
scored_titles = [ri.item.title for ri in result.ranked[:10]]
heat_titles = [it.title for it in heat]
check("对照组有 10 条", len(heat_titles) == 10)
check(
    "打分不是热度的复读",
    scored_titles != heat_titles,
    f"scored[:3]={scored_titles[:3]} heat[:3]={heat_titles[:3]}",
)


print("\n[Lab 7] 反馈闭环能记录")
tmp = Path(tempfile.mkdtemp())
store = Store(tmp / "fb.db")
it = cands[0]
store.upsert_items([it])
store.record_feedback(it.content_hash, "2026-08-25-am", 1)
rows = store.list_feedback("2026-08-25-am")
check("写入 1 条反馈", len(rows) == 1 and rows[0]["label"] == 1)
store.record_feedback(it.content_hash, "2026-08-25-am", -1)
rows = store.list_feedback("2026-08-25-am")
check("同一期可覆盖", rows[0]["label"] == -1)
store.put_embedding(it.content_hash, vecs[0], model="tfidf-cngram")
got = store.get_embedding(it.content_hash, model="tfidf-cngram")
check("SQLite 能存向量", got is not None and got.shape[0] == vecs[0].shape[0])
store.close()


print("\n[Lab 7] 出报接入 + CLI")
tmp2 = Path(tempfile.mkdtemp())
st = Store(tmp2 / "ed.db")
st.upsert_items(cands[:12])
st.upsert_items(
    [
        Item(
            Source.ZHIHU,
            Kind.ARTICLE,
            docs[i].title,
            f"https://zhuanlan.zhihu.com/p/lab7-{i}",
            content=docs[i].content,
            collector="rss_test",
            fetched_at=now,
        )
        for i in range(6)
    ]
)
ed = produce_edition(
    "am",
    st,
    out_dir=tmp2 / "am",
    boards=[],
    rss_collectors=set(),
    expected_collectors=[],
    mark=True,
)
text = ed.digest_path.read_text(encoding="utf-8")
check("digest 有头版", "# 头版" in text)
check("digest 有今日一问或深度", ("# 今日一问" in text) or ("# 深度阅读" in text))
check("写出 ranking.json", (tmp2 / "am" / "ranking.json").exists())
manifest = json.loads((tmp2 / "am" / "ranking.json").read_text(encoding="utf-8"))
check("manifest 有编号", len(manifest.get("items") or []) >= 1)
n0 = manifest["items"][0]["n"]
h0 = manifest["items"][0]["hash"]
rc = main(
    [
        "--db", str(tmp2 / "ed.db"),
        "feedback",
        "--edition", ed.edition_id,
        "--n", str(n0),
        "--label", "1",
        "--out-dir", str(tmp2 / "am"),
    ]
)
check("feedback CLI 成功", rc == 0, str(rc))
st2 = Store(tmp2 / "ed.db")
fb = st2.list_feedback(ed.edition_id)
check("CLI 写进 feedback 表", any(r["content_hash"] == h0 for r in fb), str(fb))
st2.close()
st.close()

help_text = build_parser().format_help()
for cmd in ("golden", "feedback", "ab"):
    check(f"help lists {cmd}", cmd in help_text)

rc_g = main(["golden", "--min-docs", "50"])
check("golden CLI 拟合成功", rc_g == 0, str(rc_g))

ab_dir = Path(tempfile.mkdtemp()) / "ab"
# 用上面的临时库做 A/B,不碰正在跑耐力测试的 fishnet.db
rc_ab = main(["--db", str(tmp2 / "ed.db"), "ab", "--kind", "am", "--out-dir", str(ab_dir)])
# tmp2/ed.db 已 close,重新用有数据的库
st3_path = Path(tempfile.mkdtemp()) / "ab.db"
st3 = Store(st3_path)
st3.upsert_items(cands[:40])
st3.close()
rc_ab = main(["--db", str(st3_path), "ab", "--kind", "am", "--out-dir", str(ab_dir)])
check("ab CLI 成功", rc_ab == 0, str(rc_ab))
check("ab 写出对照", (ab_dir / "compare.md").exists() and (ab_dir / "heat.md").exists())


print("\n[Lab 7] 启发式评委与探索口子")
hi = heuristic_critic("前提错了", "然而数据表明同比下降 8%,另一种解释是口径。作者不确定因果。")
lo = heuristic_critic("震惊必看", "家人们抓紧入手,不转不是中国人。性价比之王!")
check("批判文 > 营销文", hi.raw > lo.raw, f"{hi.raw} vs {lo.raw}")
check("分数在 0-10", 0 <= lo.raw <= 10 and 0 <= hi.raw <= 10)

print("\n[Lab 7] 文档")
check("lab-07 doc exists", DOC.exists())
if DOC.exists():
    t = DOC.read_text(encoding="utf-8")
    for key in ("两阶段", "黄金集", "事件聚类", "反馈", "A/B"):
        check(f"doc mentions {key}", key in t)


print(f"\n{'='*60}\n  PASSED {PASS}   FAILED {FAIL}\n{'='*60}")
sys.exit(1 if FAIL else 0)
