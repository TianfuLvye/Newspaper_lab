"""黄金集加载、画像拟合、知乎收藏夹追加。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from core.settings import ROOT, load_settings
from pipeline.embed import TfidfEmbedder
from pipeline.golden_seed import SEED
from pipeline.score import TasteProfile, age_weight

GOLDEN_YAML = ROOT / "config" / "golden.yaml"
PROFILE_PATH = ROOT / "data" / "taste" / "profile.npz"
EMBEDDER_PATH = ROOT / "data" / "taste" / "embedder.npz"


@dataclass
class GoldenDoc:
    id: str
    title: str
    content: str
    cluster: str = ""
    collected_at: datetime | None = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.content[:2000]}"

    @property
    def length(self) -> int:
        return len(self.content or self.title)

    def age_days(self, now: datetime | None = None) -> float:
        if not self.collected_at:
            return 0.0
        now = now or datetime.now(timezone.utc)
        t = self.collected_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (now - t).total_seconds() / 86400.0)


def _load_yaml(path: Path | None = None) -> dict:
    p = path or GOLDEN_YAML
    if not p.exists():
        return {"min_docs": 50, "zhihu_collections": []}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_golden(path: Path | None = None) -> list[GoldenDoc]:
    cfg = _load_yaml(path)
    docs = [
        GoldenDoc(
            id=str(d["id"]),
            title=str(d["title"]),
            content=str(d["content"]),
            cluster=str(d.get("cluster") or ""),
        )
        for d in SEED
    ]
    seed_file = cfg.get("seed")
    if seed_file:
        extra = ROOT / seed_file if not Path(seed_file).is_absolute() else Path(seed_file)
        if extra.exists():
            seen = {d.id for d in docs}
            for line in extra.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                gid = str(row.get("id") or row.get("url") or len(docs))
                if gid in seen:
                    continue
                seen.add(gid)
                ts = row.get("collected_at")
                collected = datetime.fromisoformat(ts) if ts else None
                docs.append(
                    GoldenDoc(
                        id=gid,
                        title=str(row.get("title") or ""),
                        content=str(row.get("content") or row.get("summary") or ""),
                        cluster=str(row.get("cluster") or "imported"),
                        collected_at=collected,
                    )
                )
    return docs


def append_jsonl(docs: list[GoldenDoc], dest: Path | None = None) -> int:
    cfg = _load_yaml()
    raw = cfg.get("seed") or "config/golden.jsonl"
    dest = dest or (ROOT / raw if not Path(raw).is_absolute() else Path(raw))
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if dest.exists():
        for line in dest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.add(json.loads(line).get("id"))
    n = 0
    with dest.open("a", encoding="utf-8") as f:
        for d in docs:
            if d.id in existing:
                continue
            f.write(
                json.dumps(
                    {
                        "id": d.id,
                        "title": d.title,
                        "content": d.content,
                        "cluster": d.cluster,
                        "collected_at": d.collected_at.isoformat() if d.collected_at else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n


def import_zhihu_collections(
    collection_ids: list[str] | None = None,
    *,
    rsshub_url: str | None = None,
) -> list[GoldenDoc]:
    """经 RSSHub 拉收藏夹。失败不抛,返回已成功的部分。"""
    import feedparser
    import httpx

    cfg = _load_yaml()
    ids = collection_ids if collection_ids is not None else list(cfg.get("zhihu_collections") or [])
    if not ids:
        return []
    base = (rsshub_url or load_settings().rsshub_url).rstrip("/")
    out: list[GoldenDoc] = []
    now = datetime.now(timezone.utc)
    for cid in ids:
        url = f"{base}/zhihu/collection/{cid}"
        try:
            with httpx.Client(timeout=20.0) as client:
                r = client.get(url)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
        except Exception:
            continue
        for e in parsed.entries:
            title = str(getattr(e, "title", "") or "")
            summary = str(getattr(e, "summary", "") or getattr(e, "description", "") or "")
            link = str(getattr(e, "link", "") or "")
            if not title:
                continue
            out.append(
                GoldenDoc(
                    id=link or f"zhihu-{cid}-{title[:40]}",
                    title=title,
                    content=summary,
                    cluster="zhihu_collection",
                    collected_at=now,
                )
            )
    return out


@dataclass
class FittedTaste:
    profile: TasteProfile
    embedder: TfidfEmbedder
    n_docs: int
    clusters: dict[str, int]


def fit_taste(docs: list[GoldenDoc] | None = None) -> FittedTaste:
    docs = docs if docs is not None else load_golden()
    if len(docs) < 8:
        raise ValueError(f"黄金集太小: {len(docs)} 篇,至少 8 篇才能聚类")
    embedder = TfidfEmbedder()
    embedder.fit([d.text for d in docs])
    vecs = embedder.transform([d.text for d in docs])
    weights = np.array([age_weight(d.age_days()) for d in docs], dtype=np.float64)
    profile = TasteProfile.fit(
        vecs, [d.length for d in docs], sample_weights=weights
    )
    counts: dict[str, int] = {}
    for d in docs:
        counts[d.cluster or "na"] = counts.get(d.cluster or "na", 0) + 1
    return FittedTaste(profile=profile, embedder=embedder, n_docs=len(docs), clusters=counts)


def save_taste(fitted: FittedTaste, dest: Path | None = None) -> Path:
    dest = dest or PROFILE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    p = fitted.profile
    np.savez(
        dest,
        centroids=p.centroids,
        cluster_sizes=p.cluster_sizes,
        mu_len=np.array(p.mu_len),
        sigma_len=np.array(p.sigma_len),
        k=np.array(p.k),
        n_docs=np.array(fitted.n_docs),
    )
    ed = fitted.embedder
    vocab_keys = np.array(list(ed.vocab.keys())) if ed.vocab else np.array([])
    vocab_idx = np.array(list(ed.vocab.values())) if ed.vocab else np.array([])
    np.savez(
        EMBEDDER_PATH,
        dim=np.array(ed.dim),
        nmin=np.array(ed.nmin),
        nmax=np.array(ed.nmax),
        vocab_keys=vocab_keys,
        vocab_idx=vocab_idx,
        idf=ed.idf if ed.idf is not None else np.array([]),
        proj=ed.proj if ed.proj is not None else np.array([]),
    )
    return dest


def load_taste(path: Path | None = None) -> FittedTaste | None:
    path = path or PROFILE_PATH
    if not path.exists() or not EMBEDDER_PATH.exists():
        return None
    raw = np.load(path, allow_pickle=False)
    profile = TasteProfile(
        centroids=raw["centroids"],
        cluster_sizes=raw["cluster_sizes"],
        mu_len=float(raw["mu_len"]),
        sigma_len=float(raw["sigma_len"]),
        k=int(raw["k"]),
    )
    er = np.load(EMBEDDER_PATH, allow_pickle=True)
    keys = [str(x) for x in er["vocab_keys"].tolist()]
    idx = [int(x) for x in er["vocab_idx"].tolist()]
    vocab = dict(zip(keys, idx))
    proj = er["proj"]
    embedder = TfidfEmbedder(
        dim=int(er["dim"]),
        nmin=int(er["nmin"]),
        nmax=int(er["nmax"]),
        vocab=vocab,
        idf=er["idf"].astype(np.float32),
        proj=None if proj.size == 0 else proj.astype(np.float32),
    )
    return FittedTaste(profile=profile, embedder=embedder, n_docs=int(raw["n_docs"]), clusters={})


def get_or_fit_taste() -> FittedTaste:
    got = load_taste()
    if got is not None:
        return got
    fitted = fit_taste()
    save_taste(fitted)
    return fitted
