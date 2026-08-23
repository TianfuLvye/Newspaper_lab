# Fishnet 参考实现

配套 `Fishnet-Lab.md` / `Fishnet-Lab-Answers.md`。

设计笔记（每个 Lab 完成了什么、为什么这样实现）见 [`docs/`](./docs/README.md)。

## 环境

```bash
uv sync
# Lab 1:自部署 DailyHotApi
docker run -d --name dailyhot -p 6688:6688 \
  -e ALLOWED_DOMAIN='*' -e ALLOWED_HOST=0.0.0.0 \
  imsyy/dailyhot-api:latest
# Lab 3: RSSHub + Redis
docker compose up -d
# Lab 3.4 公众号（可选）: 见 docs/lab-03-rsshub.md
# docker compose -f docker-compose.yml -f docker-compose.wewe-rss.yml up -d
```

## 常用命令

```bash
uv run main.py collect --only-hotlist       # 只跑热榜
uv run main.py collect --only-rss           # 只跑订阅
uv run main.py collect --only-targeted      # Lab 4 小红书创作者(不进默认 collect)
uv run main.py collect                      # 热榜 + RSS
uv run main.py stats
uv run main.py render --section hotlist
uv run main.py render --section subscriptions
uv run main.py render --section all
```

## 验收测试

```bash
uv run python -m tests.test_all        # Lab 0/2/6/7 核心单测
uv run python -m tests.test_lab1       # Lab 1 逻辑 + API 连通性
uv run python -m tests.test_lab2       # Lab 2 关键词 DSL + keywords.yaml
uv run python -m tests.test_lab3       # Lab 3 RSS / 订阅版面
uv run python -m tests.test_lab4       # Lab 4 MediaCrawler 隔离 / fixture 入库

# 长时间稳定性(验收标准:6 小时无崩溃)
uv run python -m tests.test_lab1_endurance --hours 6

# 开发冒烟(~3 分钟)
uv run python -m tests.test_lab1_endurance --minutes 3 --interval 60
```

依赖:`numpy`、`scikit-learn`、`PyYAML`、`httpx`、`feedparser` 等(见 `pyproject.toml`)。

## 已实现

| 模块 | Lab | 说明 |
|---|---|---|
| core/schema.py | 0 | Item 契约、URL/标题归一化、时区强制 |
| core/store.py | 0/1/6 | 幂等入库、WAL、快照、蹿升检测、健康度 |
| core/base.py | 0/6 | Collector 契约、失败隔离安全壳 |
| core/registry.py | 1/3/4 | 从 sources.yaml 实例化热榜网 + RSS + 定向采集 |
| collectors/hotlist_generic.py | 1 | DailyHotApi 通用热榜采集器 |
| collectors/rss_generic.py | 3 | 通用 RSS / RSSHub 采集器 |
| collectors/targeted_xhs.py | 4 | 子进程调 MediaCrawler,jsonl → Item |
| render/hotlist.py | 1 | 今日新上榜 Top 20 → hotlist.md |
| render/subscriptions.py | 3 | 订阅更新 → subscriptions.md |
| pipeline/keyword.py | 2 | must/any/exclude/weight/sections/aliases + filter_matched |
| config/keywords.yaml | 2 | ≥5 组关键词（财经/政经/AI） |
| docker-compose.yml | 3 | RSSHub + Redis |
| docker-compose.wewe-rss.yml | 3.4 | 可选 WeWe RSS（公众号 → RSS） |
| docs/adr/002-wechat-mp-strategy.md | 3.4 | 公众号方案 ADR(已接入章北海) |
| docs/adr/003-mediacrawler-scope.md | 4 | MediaCrawler 只覆盖指定小红书创作者 |
| docs/notes/anti-crawling.md | 4.2 | 登录态 / 签名 / Playwright / 限速 |
| docs/adr/001-why-not-trendradar.md | 2 | 不把 TrendRadar 当数据源的 ADR |
| pipeline/dedup.py | 7 | SimHash + 鸽笼分桶 + 语义聚类 |
| pipeline/score.py | 7 | 多簇兴趣画像、对数正态长度分、探索机制 |

## 仍待实现

- Lab 4 直播抓取:本机扫码 MediaCrawler + 填写 `targeted.creator_id`(fixture 路径已验收)
- `enrich/extract.py` —— 正文抽取(需 trafilatura)
- `render/*` 完整报纸 —— Jinja2 → Markdown → PDF(Lab 8)
- `notify/*` —— 邮件 / Telegram 推送
- `scheduler/run.py` —— APScheduler 编排
