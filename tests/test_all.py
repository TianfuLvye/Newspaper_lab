"""验收测试 —— 对应各 Lab 的「验收标准」。

运行: python -m tests.test_all   (无需 pytest,便于快速自检)
"""
import sys, os, tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.schema import Item, Source, Kind, normalize_url, normalize_title
from core.store import Store
from core.base import BaseCollector, run_collector, EmptyResultError
from pipeline.keyword import KeywordEngine, KeywordGroup
from pipeline.dedup import simhash, hamming, dedup_by_simhash, SimHashIndex
from pipeline.score import TasteProfile, final_score, Weights, hot_score, apply_exploration

PASS = FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}  {extra}")


# ============================================================ Lab 0
print("\n[Lab 0] URL 归一化 —— 验收标准: 8 个 case")
cases = [
    ("https://www.zhihu.com/question/123/answer/456?utm_source=wechat&utm_medium=social",
     "https://www.zhihu.com/question/123/answer/456"),
    ("https://zhihu.com/question/123/answer/456/", 
     "https://www.zhihu.com/question/123/answer/456"),
    ("http://m.zhihu.com/question/123/answer/456#comment",
     "https://www.zhihu.com/question/123/answer/456"),
    ("https://www.bilibili.com/video/BV1xx?spm_id_from=333.999&vd_source=abc",
     "https://www.bilibili.com/video/BV1xx"),
    ("https://www.bilibili.com/video/BV1xx?p=2&from=search",
     "https://www.bilibili.com/video/BV1xx?p=2"),
    ("https://mp.weixin.qq.com/s?__biz=MzA5&mid=100&idx=1&sn=abc&chksm=xyz&scene=27",
     "https://mp.weixin.qq.com/s?__biz=MzA5&idx=1&mid=100&sn=abc"),
    ("//weibo.com/1234/ABCD?type=comment",
     "https://weibo.com/1234/ABCD?type=comment"),
    ("https://m.weibo.cn/detail/999?from=timeline&isappinstalled=0",
     "https://weibo.com/detail/999?isappinstalled=0"),
]
for raw, want in cases:
    got = normalize_url(raw)
    check(f"{raw[:52]:52s}", got == want, f"\n        got={got}\n        want={want}")

print("\n[Lab 0] 标题归一化")
check("标点差异归一", normalize_title("宇树科技发布新机器人!") == normalize_title("宇树科技发布新机器人"))
check("热榜序号剥离", normalize_title("1. 某某事件") == normalize_title("某某事件"))
check("不同内容不相等", normalize_title("A事件") != normalize_title("B事件"))

print("\n[Lab 0] content_hash 稳定性 —— 排名变化不得产生新条目")
a = Item(Source.WEIBO, Kind.HOTLIST, "某热搜", "https://weibo.com/x?utm_source=a", rank=1, heat=100.0)
b = Item(Source.WEIBO, Kind.HOTLIST, "某热搜", "https://weibo.com/x", rank=17, heat=999.0)
check("rank/heat 不参与 hash", a.content_hash == b.content_hash)
c = Item(Source.ZHIHU, Kind.HOTLIST, "某热搜", "https://weibo.com/x")
check("跨 source 不混淆", a.content_hash != c.content_hash)
d = Item(Source.WEIBO, Kind.HOTLIST, "无链接热搜", "")
check("无 URL 时退化到标题", len(d.content_hash) == 32)

print("\n[Lab 0] 时区 —— naive datetime 必须被转成 aware UTC")
e = Item(Source.NEWS, Kind.ARTICLE, "t", "https://a.com/1",
         published_at=datetime(2026, 8, 6, 12, 0, 0))
check("naive 视作 CST 转 UTC", e.published_at.tzinfo is not None and e.published_at.hour == 4,
      f"got {e.published_at}")

print("\n[Lab 0] 幂等入库 —— 连跑三次库里仍是 1 条")
tmp = tempfile.mkdtemp()
st = Store(os.path.join(tmp, "t.db"))
it = lambda: Item(Source.OTHER, Kind.ARTICLE, "Hello Fishnet",
                  "https://example.com/?utm_source=x", collector="dummy")
r = [st.upsert_items([it()]) for _ in range(3)]
check("三次 upsert 结果 (1,0)(0,1)(0,1)", r == [(1, 0), (0, 1), (0, 1)], str(r))
check("库内仅 1 条", st.stats()["items"] == 1)

print("\n[Lab 0] 重复入库时补齐缺失正文(而非丢弃)")
st.upsert_items([Item(Source.OTHER, Kind.ARTICLE, "Hello Fishnet",
                      "https://example.com/", content="正文来了", collector="enricher")])
got = st.query_items()[0]
check("content 被回填", got.content == "正文来了", str(got.content))


# ============================================================ Lab 6
print("\n[Lab 6] 失败隔离 —— collector 抛异常不得中断调用方")
class Bad(BaseCollector):
    name, interval_minutes = "bad", 30
    def collect(self):
        raise RuntimeError("页面改版了")

class Empty(BaseCollector):
    name, interval_minutes = "empty", 30
    def collect(self):
        return iter([])

class Good(BaseCollector):
    name, interval_minutes, board = "good", 30, "weibo"
    def collect(self):
        for i in range(5):
            yield Item(Source.WEIBO, Kind.HOTLIST, f"热搜{i}",
                       f"https://weibo.com/h{i}", rank=i + 1, heat=1000.0 * (5 - i),
                       collector="good")

try:
    run_collector(Bad(), st); run_collector(Empty(), st); run_collector(Good(), st)
    check("异常被安全壳吞掉", True)
except Exception as ex:
    check("异常被安全壳吞掉", False, repr(ex))

h = {x["collector"]: x for x in st.health()}
check("失败被记录", h["bad"]["ok_runs"] == 0)
check("空结果算失败(页面改版信号)", h["empty"]["ok_runs"] == 0)
check("正常 collector 成功", h["good"]["ok_runs"] == 1 and h["good"]["new_items"] == 5)

print("\n[Lab 1] 热榜快照 / 新上榜检测")
snaps = st.newly_entered("weibo", window_hours=6)
check("5 条全部算新上榜", len(snaps) == 5, str(len(snaps)))

print("\n[Lab 6] used_in —— 早报内容不得在晚报重复")
hs = [i.content_hash for i in st.query_items()[:2]]
st.mark_used(hs, "2026-08-06-am")
left = [i.content_hash for i in st.query_items(unused_only=True)]
check("已用条目不再进入候选", all(x not in left for x in hs))


# ============================================================ Lab 2
print("\n[Lab 2] 关键词 DSL")
eng = KeywordEngine([
    KeywordGroup(name="自选股", must=["宁德时代"],
                 any=["财报", "定增", "订单"], exclude=["股吧", "荐股"],
                 weight=3.0, sections=["finance"]),
    KeywordGroup(name="AI", any=["大模型", "OpenAI", "AI"],
                 exclude=["割韭菜", "培训班"], weight=2.0, sections=["tech"],
                 aliases={"AI": ["人工智能", "artificial intelligence"]}),
])
check("must+any 命中", eng.match("宁德时代发布三季度财报")[0].group == "自选股")
check("must 缺失不命中", not any(r.group == "自选股" for r in eng.match("比亚迪财报")))
check("exclude 一票否决", not any(r.group == "自选股" for r in eng.match("宁德时代财报 股吧热议")))
check("别名生效", any(r.group == "AI" for r in eng.match("人工智能新进展")))
check("英文词边界(AI 不命中 SAID)", not any(r.group == "AI" for r in eng.match("HE SAID SOMETHING")))
check("标题命中权重更高",
      eng.match("大模型突破")[0].weight > eng.match("其他事", "正文提到大模型")[0].weight)
check("空组不匹配一切", KeywordEngine([KeywordGroup(name="x")]).match("任意内容") == [])
check("score 有饱和", 0 < eng.score("宁德时代财报") < 1)


# ============================================================ Lab 7 去重
print("\n[Lab 7] SimHash 近似去重")
t1 = "宁德时代发布新一代麒麟电池 能量密度大幅提升"
t2 = "宁德时代发布新一代麒麟电池，能量密度大幅提升。"   # 转载, 仅标点差异
t3 = "宁德时代今日发布新一代麒麟电池 官方称能量密度提升明显"  # 改写转载
t4 = "上海今天下暴雨 多条地铁线路停运"
check("完全转载距离 <= 3", hamming(simhash(t1), simhash(t2)) <= 3,
      str(hamming(simhash(t1), simhash(t2))))
# ★ 这是一条「反向验收」:SimHash 抓不到改写稿,这正是它的已知边界。
# 实测汉明距离约 19,远超阈值 3。请把这个数字记住——它就是你
# 必须再做一层 embedding 语义聚类(L3)的实证理由,而不是我说要做你就做。
_d13 = hamming(simhash(t1), simhash(t3))
check(f"改写转载 SimHash 抓不到(实测距离 {_d13}, 这是预期行为)", _d13 > 3, str(_d13))
check("改写稿仍比无关内容近", _d13 < hamming(simhash(t1), simhash(t4)),
      f"{_d13} vs {hamming(simhash(t1), simhash(t4))}")
check("无关内容距离远", hamming(simhash(t1), simhash(t4)) > 20,
      str(hamming(simhash(t1), simhash(t4))))

items = [
    Item(Source.NEWS, Kind.ARTICLE, t1, "https://a.com/1", collector="x", score=0.9),
    Item(Source.NEWS, Kind.ARTICLE, t2, "https://b.com/2", collector="x", score=0.5),
    Item(Source.NEWS, Kind.ARTICLE, t4, "https://c.com/3", collector="x", score=0.7),
]
kept, folded = dedup_by_simhash(items)
check("转载被折叠", len(kept) == 2, f"kept={len(kept)}")
check("高分者当主稿", kept[0].title == t1)
check("折叠关系被保留(可显示为相关报道)", len(folded) == 1)

print("\n[Lab 7] 分桶索引正确性(鸽笼原理)")
idx = SimHashIndex(threshold=3)
for i, t in enumerate([t1, t2, t3, t4]):
    idx.add(str(i), t)
check("分桶能召回近似项", "1" in idx.find_dupes("0"))
check("分桶不误召回无关项", "3" not in idx.find_dupes("0"))


# ============================================================ Lab 7 打分
print("\n[Lab 7] TasteProfile —— 多簇 vs 单一质心")
import numpy as np
rng = np.random.default_rng(0)
d = 32
# 构造三簇兴趣: 分布式系统 / 社会学 / 近代史
anchors = rng.normal(size=(3, d)); anchors /= np.linalg.norm(anchors, axis=1, keepdims=True)
fav_vecs = np.vstack([a + 0.15 * rng.normal(size=(20, d)) for a in anchors])
fav_lens = list(rng.lognormal(mean=np.log(2500), sigma=0.4, size=60).astype(int))
tp = TasteProfile.fit(fav_vecs, fav_lens)
check("聚出多个簇", tp.k >= 2, f"k={tp.k}")

on_topic = anchors[0] + 0.15 * rng.normal(size=d)
off_topic = rng.normal(size=d)
mean_c = fav_vecs.mean(axis=0); mean_c /= np.linalg.norm(mean_c)
v = on_topic / np.linalg.norm(on_topic)
check("多簇 max 优于单一平均质心",
      tp.sim(on_topic) > float(mean_c @ v),
      f"multi={tp.sim(on_topic):.3f} mean={float(mean_c @ v):.3f}")
check("命中兴趣 > 无关内容", tp.sim(on_topic) > tp.sim(off_topic))

print("\n[Lab 7] 长度打分(对数正态)")
check("正好在舒适区得分接近 1", tp.len_score(2500) > 0.9, f"{tp.len_score(2500):.3f}")
check("水贴衰减", tp.len_score(80) < 0.3, f"{tp.len_score(80):.3f}")
check("裹脚布衰减", tp.len_score(60000) < 0.4, f"{tp.len_score(60000):.3f}")

print("\n[Lab 7] 总分组合")
fresh_hi = final_score(sim=.8, len_s=.9, llm=.8, hot=.5, kw=.6, age_hours=1)
stale_hi = final_score(sim=.8, len_s=.9, llm=.8, hot=.5, kw=.6, age_hours=72)
check("时效是乘性否决因子", stale_hi.total < 0.15 * fresh_hi.total,
      f"{stale_hi.total:.4f} vs {fresh_hi.total:.4f}")
d2 = final_score(sim=.8, len_s=.9, llm=.8, hot=.5, kw=.6, dup_count=2, age_hours=1)
d20 = final_score(sim=.8, len_s=.9, llm=.8, hot=.5, kw=.6, dup_count=20, age_hours=1)
check("dup 惩罚是 log 而非线性",
      (d2.total - d20.total) < 3 * (fresh_hi.total - d2.total),
      f"{fresh_hi.total:.3f}/{d2.total:.3f}/{d20.total:.3f}")
am = final_score(sim=.3, len_s=.2, llm=.5, hot=.95, kw=.3, w=Weights.morning(), age_hours=2)
pm = final_score(sim=.3, len_s=.2, llm=.5, hot=.95, kw=.3, w=Weights.evening(), age_hours=2)
check("早报更吃热度", am.total > pm.total, f"am={am.total:.3f} pm={pm.total:.3f}")
check("hot_score 对热度做 log 压缩", hot_score(1e6) - hot_score(1e5) < 0.25)

print("\n[Lab 7] 探索机制(信息茧房解药)")
ranked = []
for i in range(60):
    sim_v = 0.9 - i * 0.012
    llm_v = 0.95 if i > 40 else 0.6         # 尾部有高质量陌生内容
    ranked.append((f"item{i}", final_score(sim=sim_v, len_s=.5, llm=llm_v,
                                           hot=.5, kw=.3, age_hours=1)))
sel = apply_exploration(ranked, n_slots=20, explore_ratio=0.15, seed=1)
check("版位数不变", len(sel) == 20, str(len(sel)))
check("确有低相似度高质量内容入选",
      any(x[1].parts["sim"] < 0.55 and x[1].parts["llm"] >= 0.7 for x in sel))

st.close()
print(f"\n{'='*60}\n  PASSED {PASS}   FAILED {FAIL}\n{'='*60}")
sys.exit(1 if FAIL else 0)
