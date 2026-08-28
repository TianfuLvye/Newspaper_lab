# Fishnet 项目结构（Lab 8 之后）

配套手册是仓库上一级的 `Fishnet-Lab.md`。本仓库 `fishnet-reference/` 是实现。设计决策按 Lab 记在 `docs/lab-*.md` 和 `docs/adr/`；本文只画**现在代码实际长什么样**。

---

## 1. 一句话

Fishnet 是一份个人报纸流水线：多源撒网 → SQLite 幂等入库 → 抽正文 → 按口味打分出一期 Markdown 早报/晚报。Lab 0–7 已经把「今天读什么」算完并写成 `digest.md`。Lab 8 要做的是把这份 Markdown **排成愿意早餐时读的 PDF**，不要再去改采集和打分。

---

## 2. 工作区怎么摆

```text
fishnet-lab/                      ← Cursor 工作区
├── Fishnet-Lab.md                实验手册（Lab 0–9 目标与验收）
├── Fishnet-Lab-Answers.md        思考题参考
└── fishnet-reference/            ← 本实现（git 仓库）
    ├── main.py                   唯一 CLI
    ├── config/                   源、关键词、黄金集、调度参数
    ├── core/                     契约 + 存储 + 采集器注册
    ├── collectors/               热榜 / RSS / 小红书定向
    ├── enrich/                   缺正文时再抽
    ├── pipeline/                 关键词、打分、出报、体检
    ├── render/                   Markdown 版面（还不是 PDF）
    ├── scheduler/                APScheduler 常驻
    ├── notify/                   Lab 9 占位（空）
    ├── docs/                     每个 Lab 的设计笔记 + 本文
    ├── tests/
    └── data/                     运行时产物（不进 git）
```

依赖外部进程，不写进 Python 包里：

| 服务 | 干什么 | 怎么起 |
|---|---|---|
| DailyHotApi `:6688` | 微博/知乎/抖音等热榜 | `docker run … imsyy/dailyhot-api` |
| RSSHub `:1200` + Redis | 知乎回答、B 站、见闻等订阅 | `docker compose up -d` |
| WeWe RSS（可选） | 公众号 → RSS | `docker-compose.wewe-rss.yml` |
| MediaCrawler（可选） | 指定小红书创作者 | 独立仓库，子进程调用 |

---

## 3. 数据怎么流

```text
config/sources.yaml
        │
        ▼
collectors/*  ──►  Item  ──►  Store (data/fishnet.db)
                                      │
                         enrich/extract.py（缺 content 才跑）
                                      │
                    ┌─────────────────┴─────────────────┐
                    │         produce_edition            │
                    │  Lab 7 打分 → 头版/深度/今日一问   │
                    │  Lab 1 热榜新上榜                  │
                    │  Lab 3 订阅（已上头版的只留目录）  │
                    │  Lab 6 系统体检                    │
                    └─────────────────┬─────────────────┘
                                      ▼
                    data/editions/{YYYY-MM-DD-am|pm}/
                                      │
                                      ▼
                         Lab 8：digest.md → A3 矩阵 PDF/HTML
                                      │
                                      ▼
                         Lab 9：邮件 / Telegram（notify/ 仍空）
```

原则：**采集器全量入库，报纸才决定今天印哪 30 条。** 热榜标题流和 B 站视频不进个性化打分池。

---

## 4. 目录说明

### 4.1 `core/` —— 全系统 ABI

| 文件 | 职责 |
|---|---|
| `schema.py` | 唯一数据结构 `Item`。`Source` / `Kind` 枚举、URL/标题归一化、`content_hash` |
| `store.py` | SQLite WAL。幂等 `INSERT OR IGNORE`，重复只补 `content`、刷 `rank/heat` |
| `base.py` | `Collector` 契约；`run_collector` 失败隔离，不让一张网拖死整次 collect |
| `registry.py` | 读 `sources.yaml`，实例化热榜 + RSS +（可选）小红书 |
| `settings.py` | `settings.toml` + yaml 加载 |
| `text.py` | HTML→段落、知乎文末裁剪、日报标题展开、出报用刊出时间 |

改 `schema.py` 等于改全系统接口。

`Kind`：`hotlist` / `article` / `post` / `video` / `quote`。  
个性化版只打 `article` 和 `post`，且排除热榜源和 B 站。

### 4.2 `collectors/` —— 撒网

| 文件 | Lab | 说明 |
|---|---|---|
| `dummy.py` | 0 | 假采集器，跑通全链路 |
| `hotlist_generic.py` | 1 | DailyHotApi，一种 board 一个采集器 |
| `rss_generic.py` | 3 | RSS / RSSHub；`{rsshub}` 占位替换 |
| `targeted_xhs.py` | 4 | 子进程调 MediaCrawler，**默认 collect 不跑** |

默认 `uv run main.py collect` = 热榜 + RSS。小红书要 `--only-targeted`。

### 4.3 `enrich/` —— 正文

`extract.py`：站点适配器（华尔街见闻走 API）+ trafilatura 兜底。同一 URL 24h HTML 缓存，同域名间隔 ≥1.5s。质量不够则 `content` 保持空，出报用标题 + summary。热榜问题页按 ADR-004 **不去爬回答**。`bilibili.py` 只列白名单 BV。口播转写是手动 `main.py transcript`，不进默认 enrich；早报 `prepare_oral` 滴灌当天一条进 `04_oral.md`。

### 4.4 `pipeline/` —— 加工与出报

| 文件 | Lab | 说明 |
|---|---|---|
| `keyword.py` | 2 | must/any/exclude → 标量 \(S_{kw}\)。yaml 里的 `sections:` **出报时没用** |
| `edition.py` | 6/7 | `produce_edition`：版面隔离、残缺出报、成功后才 `used_in` |
| `health.py` | 6 | 24h 从未成功 / 产出骤降 / 库水位 |
| `embed.py` | 7 | 汉字 n-gram TF-IDF，向量进 SQLite，不上 chromadb |
| `golden.py` / `golden_seed.py` | 7 | 冷启动 ≥50 篇 → `TasteProfile` |
| `score.py` | 7 | \(S_{sim}+S_{len}+S_{hot}+S_{kw}\) + 探索 + MMR |
| `critic.py` | 7 | 批判性评委；没 LLM Key 走启发式 |
| `dedup.py` | 7 | SimHash 转载折叠 + embedding 聚类 |
| `rank.py` | 7 | 两阶段召回，接到 `produce_edition` |

打分公式在 `rank.py` / `score.py`。`feedback` 只写库，**还不会改权重**。

### 4.5 `render/` —— Markdown 中间层 + Lab 8 报纸

| 文件 | 写出 |
|---|---|
| `ranked.py` | `01_headline.md` / `03_deepread.md` / `07_critical.md` |
| `oral.py` | `04_oral.md` |
| `hotlist.py` | `02_hotlist.md` |
| `subscriptions.py` | `06_subscribe.md` + `items/*.md` |
| `health.py` | `99_health.md` |
| `layout/` | A3 4×8 矩阵装箱、分页、1–3 图井 |
| `newspaper.py` | `digest.html` / `digest.pdf` / `layout.json` |
| `sections/` | 调试用碎片；`*.md` 被 gitignore |

现在没有 Jinja2 模板。Lab 8 把 `01_`…`99_` 装进 A3 矩阵,写出 `digest.pdf` / `digest.html`。版面号仍是文件名。

注意：`digest.md` 的拼接顺序是 **口播 → 头版 → 深度 → 今日一问 → 热榜 → 订阅 → 体检**。口播栏先写，避免总时限把滴灌稿跳掉。PDF 另有报纸版序:早报热榜靠前,晚报深度靠前。

### 4.6 `scheduler/` / `notify/`

- `scheduler/run.py`：热榜 30min、RSS 60min、定向与 enrich 6h；07:00 早报 / 18:00 晚报（`Asia/Shanghai`）。jitter + coalesce，避免整点齐发和补跑风暴。
- `notify/`：空。`main.py push` 是 Lab 9 占位。

---

## 5. 配置

| 文件 | 作用 |
|---|---|
| `config/settings.toml` | DailyHot / RSSHub 地址、库路径、调度时刻、抽取缓存、打分条数 |
| `config/sources.yaml` | `hotlists` + `feeds`（+ 可选 `targeted`） |
| `config/keywords.yaml` | 关键词组；只贡献 \(S_{kw}\) |
| `config/golden.yaml` | 黄金集路径、知乎收藏夹 id |
| `config/golden.jsonl` | 画像语料（gitignore，个人数据） |
| `config/wechat.yaml` | WeWe 公众号（gitignore） |
| `.env` | `ZHIHU_COOKIES`、`FISHNET_LLM_API_KEY` 等 |

`[ranking]` 当前：粗排 Top 150 进评委；头版 3、深度 8、今日一问 3；探索比 15%。

订阅源（`feeds`）现状：知乎只订**回答**（不定动态）；不定个人 B 站 UP（视频没有转写，进不了打分和订阅正文）；新番走 Bangumi 日历；华尔街日报官方 RSS + 华尔街见闻；公众号在 `wechat.yaml`。

---

## 6. 数据库

路径默认 `data/fishnet.db`。

| 表 | 用途 |
|---|---|
| `items` | 主存储。主键 `content_hash`。`used_in` = 上过哪一期 |
| `raw_payloads` | 原始 JSON，调试用 |
| `rank_snapshots` | 热榜时序，算「新上榜 / 蹿升」 |
| `collector_runs` | 每次采集/出报的成功失败 |
| `feedback` | 读完 Fnn 后的有用/无用 |
| `embeddings` | Lab 7 向量 BLOB |

时间一律 ISO8601 UTC。出报窗口看 **`published_at`，没有才退 `fetched_at`**（避免旧稿因未读积压混进今天）。

`used_in` 在 `digest.md` 落盘成功后才打。重出同一天早报需要先清掉该期号的 `used_in`，否则候选池是空的。

---

## 7. 一期报纸落在哪

`data/editions/{YYYY-MM-DD-am|pm}/`（gitignore）：

```text
digest.md          ← Lab 8 主入口：各版 Markdown 拼在一起
01_headline.md
02_hotlist.md
03_deepread.md
04_oral.md
06_subscribe.md
07_critical.md
99_health.md
ranking.json       ← F01… → content_hash，给 feedback / 以后 HTML 按钮
items/*.md         ← 单篇离线稿
```

手册里的 `05_tech.md` **还没做**。`04_oral.md` 是口播栏（合集滴灌 + 订阅 UP）。关键词组虽标了 `sections: [finance|tech|policy]`，`edition.py` 没有按这个切财经/科技版。

两层内容：

1. **个性化**（先算）：头版 / 深度 / 今日一问。候选 = 约 48h 未用、有可读正文的 `article|post`，排除热榜源、视频、B 站、知乎「赞同了」。
2. **速览**：热榜新上榜（6h）+ 订阅（48h 刊出时间）。已上头版的条目在订阅目录里写成「已上头版 F01」，不重复全文。

调试单版（**不**打 `used_in`）：

```bash
uv run main.py render --section hotlist
uv run main.py render --section subscriptions
```

正式出报（打 `used_in`）：

```bash
uv run main.py render --edition am
```

对照（不打 `used_in`）：`uv run main.py ab --kind am` → `data/editions/_ab/`。

---

## 8. CLI 对照

| 命令 | Lab | 做什么 |
|---|---|---|
| `collect` | 1/3 | 热榜 + RSS 入库 |
| `collect --only-hotlist` / `--only-rss` / `--only-targeted` | | 只跑一类网 |
| `enrich --limit 20` | 5 | 抽正文；并列出 B 站白名单 |
| `render --edition am\|pm` | 6/7 | 出一期报纸 |
| `health` | 6 | 体检（与 99 页同一份） |
| `serve` | 6 | 常驻调度 |
| `golden` | 7 | 拟合口味 |
| `feedback --edition … --n 1 --label 1\|-1` | 7 | 读完打点 |
| `ab --kind am` | 7 | 热度 vs 打分盲评 |
| `stats` | 0 | 库规模 |
| `pdf` | 8 | 已有 digest → A3 HTML/PDF,不打 used_in |
| `push` | 9 | 未实现 |

测试：`uv run python -m tests.test_labN`（N=1…8），总冒烟 `tests.test_all`。

---

## 9. 已完成的 Lab → 代码落点

| Lab | 用户能感知到的结果 | 主要代码 |
|---|---|---|
| 0 地基 | Item + SQLite + CLI | `core/schema.py` `store.py` `main.py` |
| 1 热榜 | 新上榜 Top 20 | `collectors/hotlist_generic.py` `render/hotlist.py` |
| 2 关键词 | \(S_{kw}\)，不单独成版 | `pipeline/keyword.py` `config/keywords.yaml` |
| 3 RSSHub | 订阅版 | `collectors/rss_generic.py` `render/subscriptions.py` |
| 4 小红书 | 子进程隔离（直播抓取仍待本机扫码） | `collectors/targeted_xhs.py` |
| 5 正文 | `items.content` | `enrich/extract.py` `enrich/transcript.py` `enrich/oral.py` |
| 6 调度 | 早晚自动出报 + 体检页 | `scheduler/run.py` `pipeline/edition.py` |
| 7 个性化 | 头版/深度/今日一问 + Fnn | `pipeline/rank.py` `render/ranked.py` |
| 8 渲染 | A3 矩阵报纸 PDF/HTML | `render/layout/*` `render/newspaper.py` |

文档索引见 [README.md](./README.md)。

---

## 10. Lab 8 接在哪（已写）

手册目标：Markdown → 一份早餐能读完的 PDF。版心是 A3 矩阵,不是 8.1 的流式四方案。细节见 [lab-08-render.md](./lab-08-render.md) 和 [ADR-007](./adr/007-newspaper-grid.md)。

**吃这些，不要回头打 collector：**

- 主输入：分版 `01_`…`99_`(解析文件名,不必拆 digest)
- 单篇：正文进矩形;头版导语下转,内页尽量一版装完,超长稿置后。不截断(安全阀除外)。
- 头版「今日综述」：`render/lede.py`,有 Key 调 LLM,否则抽句
- 调试：`uv run main.py pdf --edition …`(不打 `used_in`)

```text
已有的 *.md
    → render/layout 装箱(格子 + 分页 + 图井)
    → digest.html + digest.pdf
```

空版：某栏目失败时已是「本栏目今日无数据」，PDF 原样印占位块。

`05_tech` 仍未切版；财经/科技稿混在头版和深度里。口播走 `04_oral.md`。

---

## 11. 已知缺口（Lab 8 不必先修，但会印到纸上）

- 华尔街见闻转载常带「追风交易台」会员导流；文中 `~~~~` 会当成 Markdown 代码围栏，Cursor 预览会从第一篇截断。
- RSS 重复入库默认不覆盖已有 `content`（`COALESCE`），改清洗规则后旧正文不会自动变。
- `enrich` 队列按缺正文的新条目走，容易卡在头条热榜（robots 拦截）。
- 知乎 Thoughts Memo / 差评君、WSJ、微信、B 站投稿经常因 48h 刊出窗口或视频规则而空。
- B 站 **动态**（`post`）仍可进订阅；**视频**不会。
- `keywords.yaml` 的 `sections` 与报纸版面尚未接通。
- `feedback` 不参与下一期排序。
- 黄金集仍是 `golden_seed` 冷启动；真收藏要填 `golden.yaml` 后 `golden --refresh`。

---

## 12. 日常读报路径

1. （可选）`collect` → `enrich`
2. `uv run main.py render --edition am`
3. 打开 `data/editions/今天日期-am/digest.pdf`(或 `digest.html`)
4. 读完 F01：`uv run main.py feedback --edition 2026-08-26-am --n 1 --label 1`

不要对 `data/`、`.env`、`config/golden.jsonl` 做无必要的提交。
