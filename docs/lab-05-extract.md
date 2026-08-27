# Lab 5 · 正文抽取:从标题到内容

> **范围**: 站点路由 + trafilatura 兜底、HTML 缓存、礼貌抓取、质量降级、回填 `items.content`。  
> **网络**: 验收用 20 页本地 HTML;直播 `enrich` 才打真实站点。  
> **决策**: 正文来源 / robots 边界见 [ADR-004](./adr/004-extract-and-robots.md)。

## 本 Lab 完成了什么

1. **`enrich/extract.py`**: `extract(url, html=None) -> ExtractResult`,pipeline 可直接调用。
2. **站点路由**: 微信公众号 / 知乎专栏 / 澎湃走 XPath;华尔街见闻走公开 JSON API;其它走 trafilatura。
3. **礼貌**: 同 URL 24h 磁盘缓存、同域 ≥1.5s、浏览器 UA、默认遵守 robots.txt。
4. **质量门**: `quality_score`;正文太短或像导航 → 尝试 RSS `summary` 兜底,再不行才 `content=None`。
5. **入库**: `Store.update_content` / `update_images` + `uv run main.py enrich --limit 20`(逐条打印 tier / host / extractor / imgs)。
6. **配图候选**: 微信 `data-src`、知乎 `data-original`、见闻 API `image` + content `<img>`;出报再筛。
7. **测试**: `uv run python -m tests.test_lab5`(20 页语料 + 微信 override / 见闻 API / summary 兜底 / 配图去噪,不访问外网)。

## 对应验收点

| 验收 | 落点 |
|---|---|
| 20 条测试 URL 成功率 ≥ 80%,失败能降级 | `tests/fixtures/extract_pages.py` 16 条应成功 / 4 条应降级 |
| 缓存生效:重复 URL 不发网络 | `PoliteFetcher` 按 URL sha256 落盘,mtime 内直接返回 |
| `enrich/extract.py` 回填 `items.content` | `enrich_store()` + `main.py enrich` |

## 5.1 三条路线怎么选(量化对比的结论)

手册要求对 trafilatura / 站点适配器 / 自写 CSS 做对比。本仓库的做法是 **「薄适配器 + trafilatura」**,不引入 NewsCrawler 运行时:

| 路线 | 本环境怎么测 | 结论 |
|---|---|---|
| **trafilatura** | 通用新闻/博客 HTML | 覆盖广、无站点代码;微信/知乎DOM 杂时召回不稳 |
| **站点 XPath**(微信 `#js_content`、知乎专栏、澎湃) | 语料里对应 6 页 | 国内站更稳,改版就要改选择器 |
| **NewsCrawler 类平台适配器** | 只做调研,不进主依赖 | 准确但包体/登录态重,和 Lab 4 一样不该进主进程 |

没有把 NewsCrawler 当运行时:主链路要保持「httpx + trafilatura」这么轻;需要登录的页(付费墙、微信未授权)走降级,而不是再挂一套浏览器。

## 模块与函数设计笔记

### `extract` · `ExtractResult`

- **目的**: 统一出口。调用方只看 `ok` / `text` / `tier`。
- **tier**: `full` / `partial` / `meta_only` / `blocked` / `error`。报纸永远不出现空白条目——`meta_only` 就用标题 + summary。
- **刻意不做**: 不在抽取层跑 Playwright;JS 渲染失败就降级,留给以后真需要时再加。

### `quality_score`

- 长度饱和在 ~800 字,导航词和碎行扣分。
- 阈值: ≥200 字且 score≥0.35 才 `full`;否则 `content` 置空。

### `PoliteFetcher`

- 缓存目录默认 `data/html_cache`(已在 gitignore 的 `data/` 下)。
- robots.txt 404 → 允许抓(站点没声明);明确 `Disallow` → `blocked`。
- `robots_override_hosts`(默认微信 + 知乎专栏/www):仅个人订阅放行单篇。微信 `/s`;`zhuanlan.zhihu.com/p/`;`www.zhihu.com` 仅 `/p/` 或 `/question/.../answer/...`。**热榜问题页**(`/question/{id}` 无 answer)仍然 blocked。
- 注入 `httpx.Client` 便于单测用 `MockTransport`。

### 实战里本地语料测不到的两件事

直播 `enrich --limit 20` 曾经出现 `ok=0 degraded=10 blocked=10`,全是两类源:

| 现象 | 原因 | 处理 |
|---|---|---|
| 微信 `blocked` | `https://mp.weixin.qq.com/robots.txt` 对 `*` 全面 Disallow;`/s/` 被拦。WeWe 的 feed 又经常空 `content`。 | 个人订阅 override 只放行 `/s` 文章页,用 `#js_content` |
| 知乎专栏/回答 `blocked` | 知乎 robots 同样 Disallow 专栏与问题页 | override 只放行 `/p/` 与 `/answer/`;热榜问题以后点名再拉 |
| 华尔街见闻 `degraded` | 页面是 JS SPA 空壳(`#app` + webpack),trafilatura 抽不到字。他们公开了 `api.wallstreetcn.com` + `llms.txt` | 文章 `.../articles/{id}?extract=1`(含 `/member/` `/premium/`),快讯 `.../lives/{id}`;再不行用 RSS summary |
| 见闻快讯 `meta_only` | 快讯全文经常只有 80~150 字,被 `min_chars=200` 丢掉 | 对 `/livenews/` 放行短正文,记 `partial` |
| 见闻图表 `meta_only` | `/charts/{id}` 没有文章 API,RSS 摘要也往往只有一句 | 跳过 HTML,保留标题;同题通常另有 `/articles/` 条目 |

RSS 采集侧:展示用 `summary` 仍截 500 字;若没有 `content:encoded` 且全文 summary ≥80 字,把完整 summary 写入 `content`,避免快讯在入库前就被截断。`enrich` 失败 fallback 优先用已有 `content`;回写时只允许更长的正文覆盖更短的,避免 500 字摘要把 RSS 全文盖掉。被占住的条目会在 enrich 开头从 `raw_payloads` 捞回。

`uv run main.py stats` 会打印 `with_content` / `missing`。`N new, M dup` 是最近一次 **采集** 的数字,不是 enrich。B 站 / 抖音条目本来就没有网页正文,missing 会长期高于「新闻条数」。

### `enrich_store`

- 开头先从 `raw_payloads` 把被 500 字摘要占住的 `content` 捞回。
- 再处理 `content` 为空、以及配图候选缺失的 http(s) 条目。
- 回写 `content` 只允许更长的正文覆盖更短的。
- 失败隔离:一条 403 不影响下一条。
- 每条打印 `[tier] host title extractor`,方便对照实战里到底是 blocked 还是空壳 HTML。

## 本地怎么验收

```bash
uv run python -m tests.test_lab5
```

`uv run main.py enrich --limit 20` 还会先列出 `config/bilibili.yaml` 白名单里的 UP / 合集（见 [ADR-008](./adr/008-bilibili-transcript-whitelist.md)），合集全量 BV 落到 `data/bilibili_seasons/`。播放页不走 HTML 抽取。

口播转写是另一条手动命令，不走 `enrich`：

```bash
uv run python -m tests.test_transcript
uv run main.py transcript BV19d4y1D7n3
```

需要本机 **ffmpeg**、`.env` 里的 `STT_API_KEY`（资源 `volc.seedasr.auc`）和 `FISHNET_LLM_API_KEY`。产物在 `data/transcripts/`；该 BV 若已入库则同时写入 `items.content`。合集 `drip: true` 时早报 `render --edition am` 会把当天一条印到 `04_oral.md`。

```bash
uv run python -m tests.test_drip
```

## 思考题备忘

1. **付费墙 / 登录 / 纯图片**: 硬付费墙只留标题并标「需订阅」;软墙抽已公开段落并标截断;登录态只用你自己的 Cookie 且限速;纯图片不 OCR,标题 + 链接降级。分层降级,不要二元成败。
2. **本地库 vs PDF**: 抓进 SQLite 供自己检索,和个人自用空间较大;PDF 天然易转发,渲染层只放摘要 + 链接 +「为什么给你」。报纸的价值是帮你决定读什么,不是替你保存全文。

## 留给下一 Lab 的接口

- `Item.content` 可空;Lab 7 打分用 `content or summary`。VIDEO 可由 `transcript` / 滴灌写入见报稿，进 `04_oral.md`，仍不进打分池。
- `Item.images` 是候选 URL(微信 / 见闻 / 知乎)。`enrich` 对这三家缺图的条目也会再抽;出报时 LLM/启发式挑 1–3 张下载到 `editions/{id}/images/`。
- Lab 6 调度:正文抽取建议 6 小时一轮,不要跟热榜 30 分钟绑在同一 tick。
- Lab 8 排版:正文仍来自 Markdown;配图写成 `![](images/…)` 后由 PDF/HTML 读本地文件。
