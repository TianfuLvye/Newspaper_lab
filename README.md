# Fishnet 参考实现

配套 `Fishnet-Lab.md` / `Fishnet-Lab-Answers.md`。

设计笔记（每个 Lab 完成了什么、为什么这样实现）见 [`docs/`](./docs/README.md)。

## 环境

```bash
uv sync
# Lab 1 需要自部署 DailyHotApi
docker run -d --name dailyhot -p 6688:6688 \
  -e ALLOWED_DOMAIN='*' -e ALLOWED_HOST=0.0.0.0 \
  imsyy/dailyhot-api:latest
```

## 常用命令

```bash
uv run main.py collect                 # 跑 config/sources.yaml 全部热榜
uv run main.py stats                   # 分源计数
uv run main.py render --section hotlist  # 写出 render/sections/hotlist.md
```

## 验收测试

```bash
uv run python -m tests.test_all        # Lab 0/2/6/7 核心单测
uv run python -m tests.test_lab1       # Lab 1 逻辑 + API 连通性

# 长时间稳定性(验收标准:6 小时无崩溃)
uv run python -m tests.test_lab1_endurance --hours 6

# 开发冒烟(~3 分钟)
uv run python -m tests.test_lab1_endurance --minutes 3 --interval 60
```

依赖:`numpy`、`scikit-learn`、`PyYAML`、`httpx` 等(见 `pyproject.toml`)。

## 已实现

| 模块 | Lab | 说明 |
|---|---|---|
| core/schema.py | 0 | Item 契约、URL/标题归一化、时区强制 |
| core/store.py | 0/1/6 | 幂等入库、WAL、快照、蹿升检测、健康度 |
| core/base.py | 0/6 | Collector 契约、失败隔离安全壳 |
| core/registry.py | 1 | 从 sources.yaml 实例化热榜网 |
| collectors/hotlist_generic.py | 1 | DailyHotApi 通用热榜采集器 |
| render/hotlist.py | 1 | 今日新上榜 Top 20 → hotlist.md |
| pipeline/keyword.py | 2 | must/any/exclude/aliases 关键词 DSL |
| pipeline/dedup.py | 7 | SimHash + 鸽笼分桶 + 语义聚类 |
| pipeline/score.py | 7 | 多簇兴趣画像、对数正态长度分、探索机制 |

## 仍待实现

- `enrich/extract.py` —— 正文抽取(需 trafilatura)
- `render/*` 完整报纸 —— Jinja2 → Markdown → PDF(Lab 8)
- `notify/*` —— 邮件 / Telegram 推送
- `scheduler/run.py` —— APScheduler 编排
