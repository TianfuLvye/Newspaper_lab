"""向量化 —— Lab 7。

默认不用 chromadb / 大模型:一期候选是几百条,黄金集是几十条,
在这量级上「自己算余弦」就是正确工程,再挂一个向量库是给自己加运维。

默认后端是 **汉字 n-gram TF-IDF + 截断 SVD**:
- 中文不用分词器,n-gram 对「宇树 / Unitree」这类字面差也还能靠上下文词沾边
- 完全离线、确定性,单测可复现
- 以后要换 bge-large-zh,只换 Embedder 实现,TasteProfile / Ranker 不动

向量存在 SQLite BLOB(见 Store.put_embedding),检索是 numpy 矩阵乘。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np


def _ngrams(text: str, nmin: int = 2, nmax: int = 3) -> list[str]:
    s = re.sub(r"\s+", "", (text or "").lower())
    if not s:
        return ["_empty_"]
    out: list[str] = []
    for n in range(nmin, nmax + 1):
        if len(s) < n:
            out.append(s)
            continue
        out.extend(s[i : i + n] for i in range(len(s) - n + 1))
    return out


class Embedder:
    """向量器协议。fit 只在黄金集上做,transform 对候选复用同一套词汇。"""

    name: str = "base"
    dim: int = 0

    def fit(self, texts: list[str]) -> "Embedder":
        raise NotImplementedError

    def transform(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def encode(self, text: str) -> np.ndarray:
        return self.transform([text])[0]


@dataclass
class TfidfEmbedder(Embedder):
    """char n-gram TF-IDF,可选投到 dim 维。"""

    name: str = "tfidf-cngram"
    dim: int = 64
    nmin: int = 2
    nmax: int = 3
    max_features: int = 4096
    vocab: dict[str, int] | None = None
    idf: np.ndarray | None = None
    proj: np.ndarray | None = None  # (vocab, dim)

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        docs = [_ngrams(t, self.nmin, self.nmax) for t in texts]
        df: Counter[str] = Counter()
        tf_all: list[Counter[str]] = []
        for grams in docs:
            c = Counter(grams)
            tf_all.append(c)
            df.update(c.keys())
        # 高频 n-gram 优先;太稀的对 50 篇黄金集没有稳定信号
        vocab_list = [g for g, _ in df.most_common(self.max_features)]
        self.vocab = {g: i for i, g in enumerate(vocab_list)}
        n = max(len(texts), 1)
        self.idf = np.zeros(len(vocab_list), dtype=np.float32)
        for g, i in self.vocab.items():
            self.idf[i] = math.log((n + 1) / (df[g] + 1)) + 1.0
        X = self._tfidf_matrix(tf_all)
        target = min(self.dim, X.shape[0] - 1, X.shape[1])
        if target < 8:
            # 样本太少,不投影,直接用 TF-IDF
            self.proj = None
            self.dim = int(X.shape[1])
            return self
        # X ≈ U S Vt,用 Vt[:d] 做投影
        _, _, vt = np.linalg.svd(X, full_matrices=False)
        self.proj = vt[:target].T.astype(np.float32)  # (vocab, d)
        self.dim = target
        return self

    def _tfidf_matrix(self, tf_all: list[Counter[str]]) -> np.ndarray:
        assert self.vocab is not None and self.idf is not None
        x = np.zeros((len(tf_all), len(self.vocab)), dtype=np.float32)
        for r, c in enumerate(tf_all):
            total = sum(c.values()) or 1
            for g, cnt in c.items():
                j = self.vocab.get(g)
                if j is None:
                    continue
                x[r, j] = (cnt / total) * self.idf[j]
        return x

    def transform(self, texts: list[str]) -> np.ndarray:
        if self.vocab is None or self.idf is None:
            raise RuntimeError("Embedder 尚未 fit")
        tf_all = [Counter(_ngrams(t, self.nmin, self.nmax)) for t in texts]
        x = self._tfidf_matrix(tf_all)
        if self.proj is not None:
            x = x @ self.proj
        nrm = np.linalg.norm(x, axis=1, keepdims=True) + 1e-9
        return x / nrm


class HashEmbedder(Embedder):
    """确定性 hashing trick,给单测用:不依赖语料 fit。"""

    name: str = "hash"
    dim: int = 128

    def fit(self, texts: list[str]) -> "HashEmbedder":
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _ngrams(text, 2, 2):
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
                sign = 1.0 if (h >> 1) & 1 else -1.0
                out[i, h % self.dim] += sign
        nrm = np.linalg.norm(out, axis=1, keepdims=True) + 1e-9
        return out / nrm


def cosine_search(query: np.ndarray, matrix: np.ndarray, top_k: int = 20) -> list[tuple[int, float]]:
    """暴力余弦 Top-K。n<1e4 时这就是该用的检索。"""
    q = np.asarray(query, dtype=np.float32).ravel()
    q = q / (np.linalg.norm(q) + 1e-9)
    m = np.asarray(matrix, dtype=np.float32)
    m = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    sims = m @ q
    k = min(top_k, len(sims))
    idx = np.argpartition(-sims, kth=k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(int(i), float(sims[i])) for i in idx]
