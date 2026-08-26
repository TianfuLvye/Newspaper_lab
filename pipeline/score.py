r"""个性化打分 —— Lab 7 标准答案(整个系统的大脑)。

    S(x) = w1*S_sim + w2*S_len + w3*S_llm + w4*S_hot + w5*S_kw - w6*P_dup

关键设计决策与理由都写在各函数的 docstring 里,这些「为什么」比代码本身重要。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def _weighted_mean(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    ww = np.maximum(w, 1e-9)
    ww = ww / ww.sum()
    return (v * ww[:, None]).sum(axis=0)  # (d,)


def _kmeans_labels(v: np.ndarray, k: int, *, rng: np.random.Generator,
                   n_init: int = 8, max_iter: int = 40) -> np.ndarray:
    """极简 K-means。n 只有几十到几百,不需要 sklearn。"""
    n = len(v)
    best_labels = np.zeros(n, dtype=np.int32)
    best_inertia = float("inf")
    for _ in range(n_init):
        centers = v[rng.choice(n, size=k, replace=False)]
        labels = np.zeros(n, dtype=np.int32)
        for _ in range(max_iter):
            dist = ((v[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            new_labels = dist.argmin(axis=1).astype(np.int32)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            for c in range(k):
                mask = labels == c
                if mask.any():
                    centers[c] = v[mask].mean(axis=0)
        inertia = float(((v - centers[labels]) ** 2).sum())
        if inertia < best_inertia:
            best_inertia = inertia
            best_labels = labels.copy()
    return best_labels


def age_weight(age_days: float, half_life_days: float = 180.0) -> float:
    """黄金集时间衰减:半年前权重减半。"""
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


# ---------------------------------------------------------------- 兴趣画像
@dataclass
class TasteProfile:
    r"""从收藏夹学出来的「你」。

    核心决策:用 **多簇质心** 而不是单一平均向量。

    单一平均的失效原因:设你的兴趣有三簇——分布式系统、社会学观察、
    近代史。三簇质心两两余弦可能只有 0.2,取平均得到的向量落在三者
    中间的语义空地上,对任何一簇的相似度都不高,反而是「什么都沾一点
    的水文」离它最近。这是推荐系统里典型的 centroid collapse。

    正确做法:聚类后取 max-over-clusters。
    """
    centroids: np.ndarray            # (k, d) 已 L2 归一化
    cluster_sizes: np.ndarray        # (k,) 每簇样本数,用于置信加权
    mu_len: float                    # ln(长度) 的均值
    sigma_len: float                 # ln(长度) 的标准差
    k: int = 0

    @classmethod
    def fit(cls, vectors: np.ndarray, lengths: list[int], k: int | None = None,
            min_cluster: int = 3, sample_weights: np.ndarray | None = None) -> "TasteProfile":
        """从收藏夹样本拟合。

        k 的选择:样本少时聚类没意义。经验规则 k ≈ sqrt(N/2),
        并夹在 [2, 8] 之间。50 篇收藏 → k=5,合理。

        sample_weights:画像衰减,半年前的收藏权重减半(Lab 7 思考题 1)。
        不引入 sklearn:候选量和黄金集都是百这个量级,numpy K-means 够用。
        """
        v = np.asarray(vectors, dtype=np.float32)
        v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
        n = len(v)
        w = np.ones(n, dtype=np.float64) if sample_weights is None else np.asarray(
            sample_weights, dtype=np.float64
        )
        if k is None:
            k = int(np.clip(round(math.sqrt(n / 2)), 2, 8))
        k = min(k, max(1, n // min_cluster))

        if k <= 1:
            cents = _weighted_mean(v, w)[None, :]
            sizes = np.array([n])
        else:
            labels = _kmeans_labels(v, k, rng=np.random.default_rng(42))
            cents, sizes = [], []
            for c in range(k):
                mask = labels == c
                if mask.sum() < min_cluster:
                    continue          # 丢掉太小的簇,它们多半是噪声收藏
                cents.append(_weighted_mean(v[mask], w[mask]))
                sizes.append(int(mask.sum()))
            cents = np.stack(cents) if cents else _weighted_mean(v, w)[None, :]
            sizes = np.array(sizes) if sizes else np.array([n])

        cents = cents / (np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9)

        L = np.log(np.maximum(np.asarray(lengths, dtype=np.float64), 50.0))
        return cls(centroids=cents, cluster_sizes=sizes,
                   mu_len=float(L.mean()),
                   sigma_len=float(max(L.std(), 0.35)),  # 下限防止过窄
                   k=len(cents))

    def sim(self, vec: np.ndarray) -> float:
        r"""S_sim = max_j cos(e_x, c_j),并按簇大小做轻微加权。

        簇大小加权的理由:你收藏了 30 篇分布式系统、4 篇菜谱,
        那么命中菜谱簇不该和命中分布式簇拿一样的分。
        """
        e = np.asarray(vec, dtype=np.float32)
        e = e / (np.linalg.norm(e) + 1e-9)
        cos = self.centroids @ e                       # (k,)
        conf = np.sqrt(self.cluster_sizes / self.cluster_sizes.sum())
        conf = conf / conf.max()
        return float(np.max(cos * (0.7 + 0.3 * conf)))

    def len_score(self, length: int) -> float:
        r"""S_len,对数正态形状。

        \[ S_{len}(x) = \exp\left(-\frac{(\ln L_x - \mu)^2}{2\sigma^2}\right) \]

        用 ln 而非原始长度,因为文章长度是重尾分布:
        800 字和 1600 字的差异感受 ≈ 4000 字和 8000 字的差异感受。
        """
        L = math.log(max(length, 50))
        return math.exp(-((L - self.mu_len) ** 2) / (2 * self.sigma_len ** 2))


# ---------------------------------------------------------------- 其他分项
def hot_score(heat: float | None = None, rank: int | None = None,
              ref_heat: float = 1e6) -> float:
    r"""S_hot。热度和名次二选一,都没有则给中性分 0.3。

    log 压缩:热搜热度值动辄百万,线性使用会让它单独支配总分。
    """
    if heat:
        return min(1.0, math.log1p(heat) / math.log1p(ref_heat))
    if rank:
        return max(0.0, 1.0 - math.log1p(rank) / math.log1p(50))
    return 0.3


def freshness(age_hours: float, half_life: float = 12.0) -> float:
    r"""时效衰减 \(0.5^{t/T}\)。早报 T 小(重时效),晚报 T 大(容长文)。"""
    return 0.5 ** (age_hours / half_life)


# ---------------------------------------------------------------- 总分
@dataclass
class Weights:
    sim: float = 0.30
    len: float = 0.10
    llm: float = 0.30
    hot: float = 0.15
    kw: float = 0.15
    dup_penalty: float = 0.20
    fresh: float = 1.0       # 乘性,不是加性

    @classmethod
    def morning(cls) -> "Weights":
        """早报:仍偏时效,但热度不再压过口味。热榜有自己的版面。"""
        return cls(sim=0.28, len=0.10, llm=0.28, hot=0.12, kw=0.22)

    @classmethod
    def evening(cls) -> "Weights":
        """晚报:重深度与个人口味,热度让位。"""
        return cls(sim=0.35, len=0.15, llm=0.35, hot=0.05, kw=0.10)


@dataclass
class ScoreBreakdown:
    total: float
    parts: dict = field(default_factory=dict)


def final_score(*, sim: float, len_s: float, llm: float, hot: float, kw: float,
                dup_count: int = 0, age_hours: float = 0.0,
                w: Weights | None = None, half_life: float = 12.0) -> ScoreBreakdown:
    r"""组合打分。

    两个非线性设计:
    1. 时效是**乘性**因子而不是加一项。一条三天前的新闻,无论多契合
       口味都不该上今天的报纸——加性权重做不到这种「否决」效果。
    2. dup 惩罚用 log:被 2 家转载和被 20 家转载,惩罚不该差 10 倍。
    """
    w = w or Weights()
    base = (w.sim * sim + w.len * len_s + w.llm * llm
            + w.hot * hot + w.kw * kw)
    penalty = w.dup_penalty * math.log1p(max(dup_count, 0)) / math.log(5)
    total = max(0.0, base - penalty) * freshness(age_hours, half_life)
    return ScoreBreakdown(total, {
        "sim": sim, "len": len_s, "llm": llm, "hot": hot, "kw": kw,
        "dup_penalty": penalty, "fresh": freshness(age_hours, half_life),
    })


# ---------------------------------------------------------------- 探索机制
def apply_exploration(ranked: list, n_slots: int, explore_ratio: float = 0.15,
                      sim_key=lambda x: x[1].parts["sim"],
                      llm_key=lambda x: x[1].parts["llm"],
                      seed: int | None = None) -> list:
    r"""信息茧房的解药 —— Lab 7 思考题 1 的答案。

    留出 15% 的版位给「与你口味不像(低 S_sim)但客观质量高(高 S_llm)」
    的内容。这是 \epsilon-greedy 的一个变体:探索不是随机,而是
    「在高质量的陌生内容里随机」——纯随机探索只会给你垃圾,伤害体验。
    """
    import random
    rng = random.Random(seed)
    n_exp = max(1, int(n_slots * explore_ratio))
    n_exp = min(n_exp, n_slots // 2)

    main = ranked[:n_slots - n_exp]
    chosen = {id(x) for x in main}
    pool = [x for x in ranked[n_slots - n_exp:]
            if id(x) not in chosen and sim_key(x) < 0.55 and llm_key(x) >= 0.7]
    rng.shuffle(pool)
    explore = pool[:n_exp]
    if len(explore) < n_exp:  # 池子不够就用常规候选补齐,不留空位
        rest = [x for x in ranked[n_slots - n_exp:] if id(x) not in chosen
                and x not in explore]
        explore += rest[:n_exp - len(explore)]
    return main + explore


def apply_mmr(ranked: list, vectors: np.ndarray, n_slots: int,
              lambda_: float = 0.7, score_key=lambda x: x[1].total) -> list:
    r"""多样性重排。选完第 k 条后,后续按与已选最大余弦做惩罚:

    \[ \mathrm{MMR}(x)=\lambda S(x)-(1-\lambda)\max_{y\in selected}\cos(e_x,e_y) \]

    \lambda≈0.7:同一版面不会全是同一话题的不同角度。
    """
    if not ranked or n_slots <= 0:
        return []
    v = np.asarray(vectors, dtype=np.float32)
    v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
    remaining = list(range(len(ranked)))
    selected: list[int] = []
    first = max(remaining, key=lambda i: score_key(ranked[i]))
    selected.append(first)
    remaining.remove(first)
    while remaining and len(selected) < n_slots:
        best_i = remaining[0]
        best_s = -1e9
        for i in remaining:
            sim_sel = float(np.max(v[i] @ v[selected].T))
            mmr = lambda_ * float(score_key(ranked[i])) - (1.0 - lambda_) * sim_sel
            if mmr > best_s:
                best_s = mmr
                best_i = i
        selected.append(best_i)
        remaining.remove(best_i)
    return [ranked[i] for i in selected]
