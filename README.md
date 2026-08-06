# Fishnet 参考实现

配套 `Fishnet-Lab.md` / `Fishnet-Lab-Answers.md`。

## 运行验收测试

```bash
python3 -m tests.test_all      # 53 项, 无需第三方 pytest
```

依赖:`numpy`、`scikit-learn`、`PyYAML`(仅 keyword.py 的 from_yaml 需要)。

## 已实现(标答部分)

| 模块 | Lab | 说明 |
|---|---|---|
| core/schema.py | 0 | Item 契约、URL/标题归一化、时区强制 |
| core/store.py  | 0/1/6 | 幂等入库、WAL、快照、蹿升检测、健康度 |
| core/base.py   | 0/6 | Collector 契约、失败隔离安全壳 |
| pipeline/keyword.py | 2 | must/any/exclude/aliases 关键词 DSL |
| pipeline/dedup.py   | 7 | SimHash + 鸽笼分桶 + 语义聚类 |
| pipeline/score.py   | 7 | 多簇兴趣画像、对数正态长度分、探索机制 |

## 留给你实现(Lab 正文有指引)

- `collectors/*`   —— 各平台采集器(需网络)
- `enrich/extract.py` —— 正文抽取(需 trafilatura)
- `render/*`       —— Jinja2 → Markdown → PDF
- `notify/*`       —— 邮件 / Telegram 推送
- `scheduler/run.py` —— APScheduler 编排

这个划分是刻意的:**已实现的都是「不可外包」的核心逻辑,留给你的都是有成熟方案可抄的胶水层。**
