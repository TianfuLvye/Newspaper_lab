"""近似去重与事件聚类 —— Lab 7 标准答案(不依赖 embedding 的那一半)。

三层去重,成本从低到高:
  L1 精确  content_hash          O(1)      —— Lab 0 已在入库时完成
  L2 近似  SimHash 汉明距离       O(n) 分桶  —— 本文件
  L3 语义  embedding 余弦 + 聚类  O(n^2)    —— 本文件下半部分

为什么 SimHash 不够、还需要 L3:
  「宁德时代发布新电池」和「宁王新电池亮相」——字面重叠几乎为零,
  SimHash 判不出来,只有语义向量能。
  但 SimHash 便宜,先用它砍掉 80% 的转载稿,再让 L3 处理剩下的。
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

_HASH_BITS = 64


# ---------------------------------------------------------------- 分词
def tokenize(text: str) -> list[str]:
    """轻量中英混合分词。

    中文用 bigram(不引入 jieba 依赖也能有不错效果),英文按词切。
    正式项目建议换成 jieba,但 bigram 在去重场景下够用且更鲁棒——
    它不受分词器词表更新影响,历史 SimHash 值永远可比。
    """
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.lower())
    toks: list[str] = []
    for seg in text.split():
        if re.fullmatch(r"[a-z0-9]+", seg):
            toks.append(seg)
        else:
            cjk = re.findall(r"[\u4e00-\u9fff]", seg)
            toks += ["".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)]
    return toks


def simhash(text: str, bits: int = _HASH_BITS) -> int:
    """标准 SimHash。词频作权重。"""
    v = [0] * bits
    freq: dict[str, int] = defaultdict(int)
    for t in tokenize(text):
        freq[t] += 1
    if not freq:
        return 0
    for tok, w in freq.items():
        h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
        for i in range(bits):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class SimHashIndex:
    """分桶索引:把 64 位切成 4 段各 16 位,任意两段全等才比较。

    原理:若汉明距离 <= 3,则 4 段中至少有 1 段完全相同(鸽笼原理)。
    这把 O(n^2) 降到接近 O(n),n 上万时是必需的。
    """

    def __init__(self, threshold: int = 3, segments: int = 4):
        assert threshold < segments, "阈值必须小于分段数,否则鸽笼原理不成立"
        self.threshold = threshold
        self.segments = segments
        self.seg_bits = _HASH_BITS // segments
        self.buckets: list[dict[int, list[str]]] = [defaultdict(list) for _ in range(segments)]
        self.hashes: dict[str, int] = {}

    def _keys(self, h: int) -> list[int]:
        mask = (1 << self.seg_bits) - 1
        return [(h >> (i * self.seg_bits)) & mask for i in range(self.segments)]

    def add(self, key: str, text: str) -> None:
        h = simhash(text)
        self.hashes[key] = h
        for i, k in enumerate(self._keys(h)):
            self.buckets[i][k].append(key)

    def find_dupes(self, key: str) -> list[str]:
        h = self.hashes[key]
        cand: set[str] = set()
        for i, k in enumerate(self._keys(h)):
            cand.update(self.buckets[i].get(k, []))
        cand.discard(key)
        return [c for c in cand if hamming(h, self.hashes[c]) <= self.threshold]


def dedup_by_simhash(items: Sequence, key=lambda i: i.content_hash,
                     text=lambda i: f"{i.title} {i.summary or ''}",
                     score=lambda i: i.score or 0.0,
                     threshold: int = 3) -> tuple[list, dict[str, list[str]]]:
    """返回 (保留的 items, {保留者 -> 被折叠者列表})。

    折叠而非丢弃:被折叠的条目在报纸上作为「相关报道」显示,
    这既避免重复,又保留了「这件事被 N 家媒体报道」这个重要信号。
    """
    idx = SimHashIndex(threshold=threshold)
    for it in items:
        idx.add(key(it), text(it))

    order = sorted(items, key=lambda i: -score(i))  # 分高者优先当主稿
    kept, folded, taken = [], {}, set()
    for it in order:
        k = key(it)
        if k in taken:
            continue
        dupes = [d for d in idx.find_dupes(k) if d not in taken]
        kept.append(it)
        taken.add(k)
        taken.update(dupes)
        if dupes:
            folded[k] = dupes
    return kept, folded


# ---------------------------------------------------------------- 语义聚类
def cluster_by_embedding(vectors, threshold: float = 0.82) -> list[int]:
    r"""单遍层次聚类(近似 single-linkage),返回每个元素的簇 id。

    为什么不用 KMeans:事件数量每天都不同,你没法预设 k。
    为什么不用 DBSCAN:需要调 eps 和 min_samples,而余弦阈值更直观。

    阈值 0.82 是经验值。太低会把「同一领域不同事件」合并,
    太高则起不到聚类作用。建议你用自己的数据跑一遍网格看看。
    """
    import numpy as np
    v = np.asarray(vectors, dtype=np.float32)
    if len(v) == 0:
        return []
    v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
    sim = v @ v.T

    n = len(v)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    return [find(i) for i in range(n)]
