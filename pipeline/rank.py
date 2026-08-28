"""两阶段排序 —— Lab 7 接到出报 pipeline 的那一层。

    召回/粗排: S_sim + S_len + S_hot + S_kw  (全体候选)
    精排:      只对 Top 150 跑评委,再加 P_dup、探索、MMR

采集器仍然全量入库;这里才决定今天上哪 30 条。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from collectors.rss_generic import is_excluded_feed_title, title_exclude_by_collector
from core.schema import Item, Kind, Source
from core.store import Store
from core.text import (
    CST,
    display_title,
    is_title_longer_than_body,
    is_zhihu_activity_item,
    is_zhihu_daily,
    item_published_at,
    readable_body,
)
from pipeline.critic import Critic, CriticResult
from pipeline.dedup import fold_events
from pipeline.golden import FittedTaste, get_or_fit_taste
from pipeline.keyword import KeywordEngine
from pipeline.score import (
    Weights,
    apply_exploration,
    apply_mmr,
    final_score,
    hot_score,
)

COARSE_K = 150
HEADLINE_N = 3
DEEPREAD_N = 8
CRITICAL_N = 3
EXPLORE_RATIO = 0.15
MIN_SCORE = 0.12
MIN_BODY = 80
SCORE_KINDS = (Kind.ARTICLE, Kind.POST)
# 热榜平台的标题流不许进个性化版。热度再高也只该待在 02_hotlist。
HOTLIST_SOURCES = {Source.TOUTIAO, Source.DOUYIN, Source.WEIBO, Source.THEPAPER}


def item_hot(it: Item) -> float:
    """公众号 / 小红书 / 长文:热度权重视为 0 或极低,避免赞数崇拜。"""
    if it.source in (Source.WECHAT_MP, Source.XHS):
        return 0.0
    if it.kind == Kind.HOTLIST or it.source in HOTLIST_SOURCES:
        return 0.0
    if it.kind in (Kind.ARTICLE, Kind.POST, Kind.VIDEO):
        return hot_score(it.heat, it.rank) * 0.15
    return hot_score(it.heat, it.rank)


def has_readable_body(it: Item) -> bool:
    body = readable_body(it)
    return len(body) >= MIN_BODY


def is_rank_candidate(
    it: Item, *, title_exclude: dict | None = None
) -> bool:
    """个性化打分只看订阅长文,不看热榜八卦、B站视频、知乎赞同动态、广告槽。"""
    if it.kind == Kind.HOTLIST or it.kind == Kind.VIDEO:
        return False
    if it.source in HOTLIST_SOURCES or it.source == Source.BILIBILI:
        return False
    if is_zhihu_activity_item(it):
        return False
    if is_excluded_feed_title(it, patterns=title_exclude):
        return False
    if is_title_longer_than_body(it):
        return False
    if it.kind not in SCORE_KINDS:
        return False
    return has_readable_body(it)


def item_age_hours(it: Item, now: datetime) -> float:
    t = it.published_at or it.fetched_at
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 3600.0)


def item_body(it: Item) -> str:
    return readable_body(it)


@dataclass
class RankedItem:
    item: Item
    breakdown: object
    vec: np.ndarray
    critic: CriticResult | None = None
    related: list[Item] = field(default_factory=list)

    @property
    def total(self) -> float:
        return float(self.breakdown.total)


@dataclass
class RankResult:
    ranked: list[RankedItem]
    headline: list[RankedItem]
    deepread: list[RankedItem]
    critical: list[RankedItem]
    folded: dict[str, list[str]]
    n_candidates: int
    n_coarse: int
    n_llm: int
    kind: str


def _kw_engine() -> KeywordEngine:
    from core.settings import ROOT

    return KeywordEngine.from_yaml(ROOT / "config" / "keywords.yaml")


def _pair_keys(ri: RankedItem):
    return (ri, ri.breakdown)


def rank_items(
    items: list[Item],
    *,
    kind: str = "am",
    taste: FittedTaste | None = None,
    critic: Critic | None = None,
    kw: KeywordEngine | None = None,
    now: datetime | None = None,
    coarse_k: int = COARSE_K,
    write_store: Store | None = None,
) -> RankResult:
    """对候选打分。不改 used_in。"""
    now = now or datetime.now(timezone.utc)
    taste = taste or get_or_fit_taste()
    critic = critic or Critic()
    kw = kw or _kw_engine()
    w = Weights.morning() if kind == "am" else Weights.evening()
    half = 8.0 if kind == "am" else 24.0

    if not items:
        return RankResult([], [], [], [], {}, 0, 0, 0, kind)

    title_exclude = title_exclude_by_collector()
    items = [it for it in items if is_rank_candidate(it, title_exclude=title_exclude)]
    if not items:
        return RankResult([], [], [], [], {}, 0, 0, 0, kind)

    texts = [it.text_for_embedding for it in items]
    vecs = taste.embedder.transform(texts)
    if write_store is not None:
        for it, vec in zip(items, vecs):
            write_store.put_embedding(it.content_hash, vec, model=taste.embedder.name)

    coarse: list[RankedItem] = []
    for it, vec in zip(items, vecs):
        sim = taste.profile.sim(vec)
        len_s = taste.profile.len_score(it.length)
        hot = item_hot(it)
        kws = kw.score(it.title, item_body(it))
        bd = final_score(
            sim=sim, len_s=len_s, llm=0.5, hot=hot, kw=kws,
            age_hours=item_age_hours(it, now), w=w, half_life=half,
        )
        it.score = bd.total
        coarse.append(RankedItem(item=it, breakdown=bd, vec=vec))
    coarse.sort(key=lambda x: -x.total)

    top = coarse[: min(coarse_k, len(coarse))]
    for ri in top:
        cached = ri.item.llm_summary
        cr = critic.judge(ri.item.title, item_body(ri.item), cached=cached)
        ri.critic = cr
        bd = final_score(
            sim=ri.breakdown.parts["sim"],
            len_s=ri.breakdown.parts["len"],
            llm=cr.score01,
            hot=ri.breakdown.parts["hot"],
            kw=ri.breakdown.parts["kw"],
            age_hours=item_age_hours(ri.item, now),
            w=w,
            half_life=half,
        )
        ri.breakdown = bd
        ri.item.score = bd.total
        ri.item.llm_summary = cr.as_json()
        if write_store is not None:
            write_store.update_ranking(
                ri.item.content_hash, score=bd.total, llm_summary=cr.as_json()
            )

    # 精排后再做事件折叠,主稿带上相关报道
    top_items = [ri.item for ri in top]
    top_vecs = np.stack([ri.vec for ri in top])
    kept, folded = fold_events(top_items, top_vecs, score=lambda i: i.score or 0.0)
    by_hash = {ri.item.content_hash: ri for ri in top}
    all_by_hash = {it.content_hash: it for it in items}
    folded_ranked: list[RankedItem] = []
    for it in kept:
        ri = by_hash[it.content_hash]
        rel_hashes = folded.get(it.content_hash, [])
        ri.related = [all_by_hash[h] for h in rel_hashes if h in all_by_hash]
        ri.item.cluster_id = it.content_hash
        if write_store is not None:
            write_store.update_ranking(it.content_hash, cluster_id=it.content_hash)
        folded_ranked.append(ri)
    folded_ranked.sort(key=lambda x: -x.total)

    # 绝对阈值:低分不拿来凑版。热榜标题即使混进来也不要。
    viable = [
        ri for ri in folded_ranked
        if ri.total >= MIN_SCORE and is_rank_candidate(ri.item, title_exclude=title_exclude)
    ]

    if not viable:
        return RankResult([], [], [], [], folded, len(items), len(top), critic.call_count, kind)

    n_slots = min(20, len(viable))
    as_pairs = [(_pair_keys(ri)[0], ri.breakdown) for ri in viable]
    # apply_exploration 期望 (obj, breakdown)
    explored = apply_exploration(as_pairs, n_slots=n_slots, explore_ratio=EXPLORE_RATIO, seed=7)
    order = {id(x[0]): i for i, x in enumerate(explored)}
    explored_items = sorted(viable, key=lambda ri: order.get(id(ri), 999))
    vecs_e = np.stack([ri.vec for ri in explored_items])
    diversified = apply_mmr(
        [(ri, ri.breakdown) for ri in explored_items],
        vecs_e,
        n_slots=n_slots,
    )
    ranked = [p[0] for p in diversified]

    # 版面切分:头版 / 深度 / 今日一问,互不重复
    used: set[str] = set()
    headline = _take(ranked, HEADLINE_N, used)
    deep_pool = [
        ri for ri in ranked
        if ri.item.content_hash not in used
        and ri.item.kind in (Kind.ARTICLE, Kind.POST)
        and ri.item.length >= 200
        and has_readable_body(ri.item)
    ]
    # 宁缺毋滥:没有长文就空着,绝不拿热榜标题凑「深度阅读」。
    deepread = _take(deep_pool, DEEPREAD_N, used)

    crit_pool = sorted(
        [ri for ri in ranked if ri.item.content_hash not in used],
        key=lambda ri: -(ri.critic.score01 if ri.critic else 0),
    )
    critical = _take(crit_pool, CRITICAL_N, used)
    headline, deepread, critical = ensure_todays_zhihu_daily(
        items, headline, deepread, critical, ranked, used, now
    )

    return RankResult(
        ranked=ranked,
        headline=headline,
        deepread=deepread,
        critical=critical,
        folded=folded,
        n_candidates=len(items),
        n_coarse=len(top),
        n_llm=critic.call_count,
        kind=kind,
    )


def _todays_zhihu_daily(items: list[Item], now: datetime) -> Item | None:
    """当天刊出的知乎日报早报。有多份时取最新一篇。"""
    today = now.astimezone(CST).date()
    found: list[Item] = []
    for it in items:
        if not is_zhihu_daily(it) or not has_readable_body(it):
            continue
        t = item_published_at(it)
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t.astimezone(CST).date() == today:
            found.append(it)
    if not found:
        return None
    return max(found, key=lambda it: item_published_at(it) or datetime.min.replace(tzinfo=timezone.utc))


def _pin_breakdown() -> object:
    from pipeline.score import ScoreBreakdown

    return ScoreBreakdown(total=0.0, parts={"sim": 0, "len": 0, "llm": 0, "hot": 0, "kw": 0})


def ensure_todays_zhihu_daily(
    items: list[Item],
    headline: list[RankedItem],
    deepread: list[RankedItem],
    critical: list[RankedItem],
    ranked: list[RankedItem],
    used: set[str],
    now: datetime,
) -> tuple[list[RankedItem], list[RankedItem], list[RankedItem]]:
    """当天日报钉进深度阅读。订阅 weight 只影响轮询,不能保证上版。"""
    pick = _todays_zhihu_daily(items, now)
    if pick is None:
        return headline, deepread, critical
    hid = pick.content_hash
    placed = headline + deepread + critical
    if any(ri.item.content_hash == hid for ri in placed):
        return headline, deepread, critical
    ri = next((x for x in ranked if x.item.content_hash == hid), None)
    if ri is None:
        dim = int(ranked[0].vec.shape[0]) if ranked else 8
        ri = RankedItem(item=pick, breakdown=_pin_breakdown(), vec=np.zeros(dim))
    deepread = [ri] + [x for x in deepread if x.item.content_hash != hid]
    while len(deepread) > DEEPREAD_N:
        dropped = deepread.pop()
        used.discard(dropped.item.content_hash)
    used.add(hid)
    return headline, deepread, critical


def _take(pool: list[RankedItem], n: int, used: set[str]) -> list[RankedItem]:
    out: list[RankedItem] = []
    for ri in pool:
        if len(out) >= n:
            break
        if ri.item.content_hash in used:
            continue
        out.append(ri)
        used.add(ri.item.content_hash)
    return out


def collect_rank_candidates(
    store: Store,
    *,
    window_hours: int = 48,
    unused_only: bool = True,
    limit: int = 3000,
) -> list[Item]:
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    fetch_since = datetime.now(timezone.utc) - timedelta(hours=max(window_hours, 72))
    items = store.query_items(
        since=fetch_since,
        kinds=list(SCORE_KINDS),
        unused_only=unused_only,
        limit=limit,
    )
    title_exclude = title_exclude_by_collector()
    out = []
    for it in items:
        t = item_published_at(it)
        if t is not None:
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t < since:
                continue
        if is_rank_candidate(it, title_exclude=title_exclude):
            out.append(it)
    return out


def rank_for_edition(
    store: Store,
    kind: str,
    *,
    window_hours: int = 48,
    critic: Critic | None = None,
    persist: bool = True,
) -> RankResult:
    items = collect_rank_candidates(store, window_hours=window_hours)
    return rank_items(
        items, kind=kind, critic=critic, write_store=store if persist else None
    )


def ranking_manifest(result: RankResult) -> dict:
    items = []
    n = 0
    for section, rows in (
        ("headline", result.headline),
        ("deepread", result.deepread),
        ("critical", result.critical),
    ):
        for ri in rows:
            n += 1
            items.append(
                {
                    "n": n,
                    "section": section,
                    "hash": ri.item.content_hash,
                    "title": display_title(ri.item),
                    "score": ri.total,
                }
            )
    return {
        "kind": result.kind,
        "n_candidates": result.n_candidates,
        "n_coarse": result.n_coarse,
        "n_llm": result.n_llm,
        "items": items,
    }


def heat_only_order(items: list[Item], n: int = 20) -> list[Item]:
    """A/B 对照组:纯热度。没有 heat 的按 rank,再没有按时间。"""

    def key(it: Item):
        t = it.published_at or it.fetched_at
        ts = t.timestamp() if t else 0.0
        return (-(it.heat or 0.0), it.rank if it.rank is not None else 999, -ts)

    return sorted(items, key=key)[:n]
