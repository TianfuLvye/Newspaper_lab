# Lab 7 · 个性化排序:把信息堆变成我的报纸

> **范围**: 黄金集画像、两阶段打分、事件折叠、探索版位、反馈记录、A/B 对照。  
> **决策**: 向量后端见 [ADR-006](./adr/006-embed-backend.md)。

## 本 Lab 完成了什么

1. **黄金集 ≥50 篇并向量化**: `pipeline/golden_seed.py` 冷启动 50 篇风格原型(AI / 机器人 / 产业财经 / 社会观察 / 学习方法)。`uv run main.py golden` 拟合成多簇 `TasteProfile`。
2. **两阶段召回**: 全体候选用 $S_{sim}+S_{len}+S_{hot}+S_{kw}$ 粗排,只对 Top 150 跑评委(`pipeline/critic.py`)。没配 LLM Key 时走同一份 rubric 的启发式,出报不中断。
3. **打分接入出报**: `produce_edition` 写出 `01_headline.md` / `03_deepread.md` / `07_critical.md`,热榜和订阅不再按「先到先得」占满版面。
4. **事件聚类**: SimHash 折叠转载,改写稿走 embedding 层次聚类,每簇只出最高分主稿,其余当「相关报道」。
5. **反馈闭环**: 每条有编号 Fnn,`uv run main.py feedback --edition … --n 1 --label 1` 写进 `feedback` 表。
6. **A/B 自评**: `uv run main.py ab --kind am` 写出纯热度 vs 打分对照,不标记 `used_in`(也不该去动正在跑的耐力测试库)。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 收藏夹黄金集 ≥50 并向量化 | `golden_seed.SEED` + `TfidfEmbedder.fit` + `TasteProfile.fit` |
| 两阶段,每期 LLM ≤150 | `rank_items` 粗排截断 `coarse_k=150`,`Critic.call_count` |
| 同一新闻多家报道被折叠 | `fold_events` = SimHash L2 + `cluster_by_embedding` L3 |
| A/B 自评:热度 vs 打分 | `main.py ab` → `heat.md` / `scored.md` / `compare.md` |
| 反馈按钮至少能记录 | `Store.record_feedback` + `ranking.json` 编号 |

## 模块与函数设计笔记

### `pipeline/score.py` · `TasteProfile.fit`

- **目的**: $S_{sim}$ 用多簇质心的 max,而不是平均向量。
- **为什么**: 兴趣是好几坨,平均会掉进簇间空地,水文反而最近(centroid collapse)。测试里有「多簇 max 优于单一平均质心」。
- **画像衰减**: `sample_weights = 0.5**(age/180d)`,半年前的收藏减半,避免把人锁在旧口味。
- **刻意不做**: 不引入 sklearn。n 是几十到几百,numpy K-means 够用。

### `pipeline/score.py` · `apply_exploration` / `apply_mmr`

- **探索**: 15% 版位留给「低相似度 + 高 LLM 分」。不是纯随机——纯随机会给你垃圾,一周后就会把开关关掉。
- **MMR**: $\lambda=0.7$,同一版面不要全是同一话题的不同角度。
- **时效乘性、dup 用 log**: 见函数 docstring,这是总分里两个非线性。

### `pipeline/embed.py` · `TfidfEmbedder`

- **目的**: 把 title+content[:2000] 变成向量,供画像和 L3 聚类。
- **为什么不用 chromadb**: 一期候选 <3000,暴力余弦就是正确检索;再挂进程是给自己加运维。向量仍落在 SQLite BLOB,以后要换 bge 只换 Embedder。
- **为什么是汉字 n-gram**: 不引入分词器词表版本问题;对「宇树 / Unitree」这种字面差,还要靠关键词 aliases 补,向量负责近义段落。

### `pipeline/critic.py` · `Critic`

- **目的**: $S_{llm}$。rubric 写在模块顶部,和手册一致。
- **成本**: 只打粗排 Top 150;已有 `llm_summary` JSON 则缓存。
- **无 API 时**: 启发式抽「然而/前提/数据」vs「震惊/必看/营销」。分数刻意保守,避免启发式支配总分。
- **接 LLM**: 环境变量 `FISHNET_LLM_API_KEY` + 可选 `FISHNET_LLM_BASE_URL` / `FISHNET_LLM_MODEL`。

### `pipeline/rank.py` · `rank_items`

- **目的**: 加工层唯一入口。Collector 仍然全量入库。
- **热度**: 公众号 / 小红书 $S_{hot}=0$;长文热度压到 0.15 倍,兴趣分来自画像和关键词,不来自赞数崇拜。
- **早晚报**: `Weights.morning()` vs `evening()`,半衰期 8h / 24h。
- **绝对阈值**: 总分 < 0.12 不拿来凑版。

### `pipeline/dedup.py` · `fold_events`

- L1 精确 hash 在入库时已经做了。
- L2 SimHash 汉明 ≤3:便宜,砍掉 80% 转载。
- L3 改写稿:手册里那对宁德时代标题汉明距离约 19,这是必须做语义聚类的实证理由,测试里会再测一次。

### `core/store.py` · `feedback` / `embeddings`

- 反馈表 Lab 0 就建好了,本 Lab 补上写入和按期查询。
- 向量表按 `content_hash` 存 BLOB,模型名一起存,换后端不会和旧向量混用。

## 本地怎么验收

不要去碰正在跑 1 小时耐力测试的那个 terminal,也不要对 `data/fishnet.db` 跑 `render --edition`(那会写 `used_in`)。用测试库:

```bash
uv run python -m tests.test_lab7
uv run python -m tests.test_all          # 含 Lab 7 打分/去重回归
uv run main.py golden                    # 拟合画像,不采集、不出报

# 耐力测试结束后再对着真实库:
uv run main.py ab --kind am --out-dir data/editions/_ab
uv run main.py render --edition am       # 会标记 used_in
uv run main.py feedback --edition YYYY-MM-DD-am --n 1 --label 1
```

## 思考题备忘

1. **茧房**: 探索版位 + MMR + 画像衰减。对立信源以后可以给固定版位,不参与排名(让它们竞争必输)。根治仍需每月人工翻「被淘汰的高分」。
2. **评委偏见**: 先做按源/长度分层统计;长度与 LLM 分若相关过高,说明它在给长度打分。有 API 之后用 30 条人工对照算 Spearman。
3. **权重**: 第一版拍脑袋(早报偏热度、晚报偏 sim/llm),原则是归一化且个人化信号占大头。有 200+ 条反馈再用逻辑回归学,config 手动值始终优先。

## 留给下一 Lab 的接口

- `data/editions/{期号}/01_headline.md` 等是 Lab 8 的版面文件;PDF 从分版 Markdown 走,`uv run main.py pdf` 只排版。
- `ranking.json` 把编号映到 hash,Lab 8 HTML 尚未做反馈按钮,对着这份文件发 `feedback` 即可。
- 头版「今日综述」由 Lab 8 `render/lede.py` 写出。
- 推送仍是 Lab 9。
