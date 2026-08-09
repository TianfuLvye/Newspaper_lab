# Lab 2 · 拆解 TrendRadar

## 本 Lab 完成了什么

1. **解剖 TrendRadar**（`/tmp/TrendRadar` 浅克隆）：关键词语法、增量检测、Actions/timeline 调度，结论写在本文末尾 §2.2。
2. **关键词 DSL 落地**: `pipeline/keyword.py`（must / any / exclude / weight / sections / aliases）+ `annotate` / `filter_matched` 加工层接口。
3. **`config/keywords.yaml`**: ≥5 组，覆盖财经、政治经济、AI/科技，每组带 exclude。
4. **ADR-001**: 明确「只借鉴设计、不把 TrendRadar 当数据源」——见 `docs/adr/001-why-not-trendradar.md`。
5. **验收测试**: `uv run python -m tests.test_lab2`（YAML 覆盖面 + 匹配语义 + 过滤接口）。

**关于验收「TrendRadar 成功推送过一次」**: 真实飞书/Telegram/邮箱推送需要你在 TrendRadar 仓库里配置 webhook / bot token（密钥不应进本仓库）。本 Lab 在 Fishnet 侧完成的是可复用的关键词引擎与 ADR；推送渠道联调请在本机 TrendRadar 目录按官方 README 配好通知后执行一次 `docker compose` 或 Actions。设计结论不依赖那一次推送是否已发生。

## 对应验收点

| 验收 | 落点 |
|---|---|
| keywords.yaml ≥5 组，盖财经/政经/AI | `config/keywords.yaml` |
| must/any/exclude/weight + 单测 | `pipeline/keyword.py` + `tests/test_lab2.py`（及 `tests/test_all.py` Lab2 段） |
| ADR-001 | `docs/adr/001-why-not-trendradar.md` |
| TrendRadar 推送一次 | 需你在 TrendRadar 实例填通知密钥（见上） |

## 模块与函数设计笔记

### `pipeline/keyword.py` · `KeywordEngine`

- **目的**: 把「我关心什么 / 我讨厌什么」收成可配置规则，在**加工层**收窄候选池。
- **为什么不放进 Collector**: 原则 1——改关键词不该动 12 个爬虫；热榜网继续全量入库。
- **must / any / exclude**: 对齐 TrendRadar 的 `+词` / 普通词 / `!词`，但用 YAML 结构化，并多了 `sections`（进哪个版面）。
- **标题加权 ×1.5**: 标题命中通常比正文捎带一句更有信息量。
- **aliases**: 先用词典补同义（宇树/Unitree），不幻想关键词 alone 能解决语义召回。
- **score 饱和**: `total/(total+2)`，避免堆很多弱组把分刷爆。

### `KeywordEngine.annotate` / `filter_matched`

- **目的**: 给后续 render / 打分一个稳定接口：先 annotate 再决定版面，而不是在 CLI 里手写循环。
- **刻意不做**: 不写回 `Item.tags`（避免隐式污染）；调用方显式处理 MatchResult。

### `config/keywords.yaml`

- **目的**: 人能读、能 diff、能持续加 exclude 的词表。
- **为什么每组都强制想 exclude**: 中文营销号噪音是主矛盾；空 exclude 的组等于欢迎垃圾。

## 本地怎么验收

```bash
uv run python -m tests.test_lab2
# 也可顺带跑旧套件里的 Lab2 段
uv run python -m tests.test_all   # 需 numpy/sklearn(Lab7);Lab2 段不依赖外网
```

试匹配（在项目根）:

```bash
uv run python -c "
from pipeline.keyword import KeywordEngine
e = KeywordEngine.from_yaml('config/keywords.yaml')
for r in e.match('宁德时代获动力电池大额订单'):
    print(r.group, r.weight, r.sections, r.hits)
"
```

## 留给下一 Lab 的接口

- Lab 3+ collector 仍全量入库；出报前 `KeywordEngine.filter_matched` 收窄。
- Lab 6 调度：关键词过滤是「收网」阶段的一步，不是「撒网」阶段。
- Lab 7：`S_kw = engine.score(...)` 已可接入总分；向量召回与关键词做并集。

---

## 附录 · Lab 2.2 精读 TrendRadar 三问

> 源码版本：浅克隆 `sansan0/TrendRadar`（阅读日 2026-08-09）。主要文件：`trendradar/core/frequency.py`、`trendradar/core/data.py`、`trendradar/core/analyzer.py`、`config/frequency_words.txt`、`config/config.yaml`、`config/timeline.yaml`、`.github/workflows/crawler.yml`。

### 1) 关键词匹配逻辑：必须词 / 可选词 / 排除词？权重？我们的 YAML 该长什么样？

**TrendRadar 怎么做**

- 配置在 `frequency_words.txt`（类 DSL 文本，不是 YAML）：
  - **普通词**：组内 OR（任一命中即可）
  - **`+词`**：必须词，组内 AND（全部命中）
  - **`!词`**：过滤词；另有 `[GLOBAL_FILTER]` 全局排除
  - **`@N`**：该组最多展示 N 条
  - 还支持 `/regex/`、`=> 显示别名`、`[组别名]`
- 匹配实现见 `matches_word_groups()`：先全局过滤 → 再过滤词 → 再逐组检查 required 全中 + normal 任一中；**只匹配标题**（`title_lower`）。
- **没有数值 weight**：一组命中就是 bool；「权重」体现在展示排序/热度统计，而不是词组分。

**我们怎么落地**

| TrendRadar | Fishnet `keywords.yaml` |
|---|---|
| `+宁德时代` | `must: ["宁德时代"]` |
| 普通词多行 | `any: [...]` |
| `!股吧` / GLOBAL_FILTER | `exclude: [...]`（先做组级；全局可后续加） |
| 无 | `weight` + 标题命中 ×1.5 |
| 无版面概念 | `sections: [finance]` |
| `=>` / 别名 | `aliases:` 字典 |

**结论**: 语义对齐 must/any/exclude，但我们需要 **结构化 YAML + 版面 + 可加成分**，所以不抄 txt DSL，只抄规则思想。

### 2) 增量 / 新增热点：靠什么判断「新」？和 Lab 1 `newly_entered` 有何异同？

**TrendRadar**

- 存储层按批次爬取；`detect_latest_new_titles`（`core/data.py`）用  
  **最新批次标题集合 − 历史批次标题集合**  
  得到「新增标题」（偏字符串/标题维度）。
- 另有 `rank_history` 记名次轨迹。
- 推送侧 `analyzer.py`：incremental 模式只处理新增；当天第一次可把整批标成新。
- 第一次抓取时逻辑上要小心「全是新」——代码里区分了「无历史」与「增量模式第一次推送」。

**Fishnet Lab 1**

- 身份是 `content_hash`（URL 优先），不是裸标题。
- `newly_entered(board, window)`：**时间窗内出现过，且窗前该 board 从未出现**——显式时间窗口，不是「上一批次」。
- `fast_rising` 用快照算 \(\Delta r\)，这是 TrendRadar 热度箭头的亲戚，但公式我们自己定。

| 维度 | TrendRadar | Fishnet Lab 1 |
|---|---|---|
| 新身份 | 标题（+源）为主 | `content_hash` |
| 新的参照 | 上一批次 / 当日历史 | 滑动时间窗 vs 窗前 |
| 用途 | 控制推送打扰 | 报纸「新上榜」版面 + 后续过滤 |

**同**: 都承认「全量榜单 ≠ 用户想看的变化」。  
**异**: 我们把身份和观测拆开（items vs rank_snapshots），更适合后面跨源去重与报纸排版。

### 3) 推送与调度：Actions cron 与统一配置怎么组织？我们需要哪些参数？

**TrendRadar**

- **唤醒时钟**:
  - GitHub Actions：`.github/workflows/crawler.yml` 里 `cron: "33 * * * *"`（默认每小时第 33 分）+ 试用期签到机制
  - Docker：`CRON_SCHEDULE`（注释称默认约 30 分钟）
- **业务时间线**: `config/timeline.yaml` + `config.yaml` 的 `schedule.preset`（`morning_evening` / `office_hours` / …）  
  控制「采集 / AI 分析 / 推送」三阶段在哪些时段开。
- **总开关 vs 时段开关**: `platforms.enabled` / `notification.enabled` 等是总闸；timeline 是「什么时候做」。
- **通知**: 多渠道账号配置在 config（飞书/钉钉/TG/邮件等），与爬虫解耦。

**我们应留下的参数（给 Lab 6/9）**

| 参数 | 为何需要 |
|---|---|
| 采集 interval（每网不同） | 热榜 30min vs RSS 更疏 |
| 出报 cron（早/晚） | 对应报纸 edition，不是「有更新就推」 |
| timezone = Asia/Shanghai | 与用户作息一致 |
| jitter / coalesce | Lab 手册已强调，防整点齐射与补跑风暴 |
| notification 总开关 | 调试时只 collect/render 不 push |
| 「增量推送 vs 日报汇总」模式 | 可学 TrendRadar report_mode，但报纸默认偏汇总 |

**不照搬**: Actions 试用签到、把推送绑死在采集同一进程里——我们要坚持 `main.py collect` / `render` / `push` 可单独调用。
