# Lab 0 · 地基

## 本 Lab 完成了什么

在写任何真实爬虫之前，先把「数据长什么样、往哪放、怎么反复采也不炸库」定死：

1. **统一数据契约** `Item`：所有采集器说同一种话（`core/schema.py`）。
2. **幂等存储**：同一内容采 100 次库里仍 1 条（`content_hash` + `INSERT OR IGNORE`）。
3. **采集器契约**：`BaseCollector.collect()` 只产出 Item，不写库、不去重、不调 LLM。
4. **失败隔离壳** `run_collector`：单张网挂了记日志，不向上抛死整个进程。
5. **CLI 入口** `main.py`：`collect / stats / render / push`，用 `DummyCollector` 跑通「采 → 存 → 看统计」。

对应愿景：后面无论热榜、RSS 还是定向爬虫，都只是往同一个漏斗里加「渔网」。

## 对应 Lab 原则

| 原则 | 落点 |
|---|---|
| 采集与加工解耦 | Collector 不写库；入库只在 `run_collector` / Store |
| 一切幂等 | `Item.compute_hash` + `Store.upsert_items` |
| 原始 payload 落盘 | `Item.raw` → `raw_payloads` 表 |
| 失败隔离 | `run_collector` try/except + `collector_runs` |
| 时间是一等公民 | `published_at` / `fetched_at` 分字段；naive 时间强制转 UTC |

## 模块与函数设计笔记

### `core/schema.py` · `Item` / `compute_hash`

- **目的**: 全系统唯一 ABI。Collector、Store、Pipeline、Render 都只认 `Item`。
- **为什么 hash 优先用归一化 URL**: 热搜标题会被平台微调；标题进主键会导致一天多条「同一事件」。URL 更像身份，标题只在无链接时退化使用。
- **为什么 rank/heat 不进 hash**: 它们是观测状态，不是身份。否则每轮采集都变新行，去重失效。
- **刻意不做**: 不在 schema 里做过滤、打分、上报纸决策——那些是加工层的事。

### `core/schema.py` · `normalize_url` / `normalize_title`

- **目的**: 让「同一内容的不同分享链接」落到同一个 hash。
- **为什么**: App 分享、网页复制、RSS 出来的 URL 常带 `utm_*` / `spm`；不剥就会假性膨胀。
- **踩坑**: 不能无脑删光 query。微信公众号的 `__biz/mid/idx/sn` **就是内容 ID**，所以用按 host 的白名单 `_ESSENTIAL_PARAMS`。

### `core/store.py` · `upsert_items`

- **目的**: 幂等写入 + 重复时刷新易变字段（rank/heat）并补齐缺失正文。
- **为什么用 `INSERT OR IGNORE` 而不是先 SELECT 再 INSERT**: 多 collector 并行时，先查后插有竞态窗口。
- **为什么开 WAL**: SQLite 默认写锁很凶；并行采集不设 WAL 容易 `database is locked`。

### `core/store.py` · `rank_snapshots` / `collector_runs`

- **目的**: 把「实体」和「观测」拆开——`items` 一条内容一行；快照表记名次曲线；`collector_runs` 记健康度。
- **为什么现在就建**: Lab 1 的「新上榜 / 蹿升」完全依赖快照；Lab 6 告警依赖 runs。地基阶段把表备好，比后面迁移便宜。

### `core/base.py` · `BaseCollector` / `run_collector`

- **目的**: 规定渔网的形状，并用安全壳统一「计时、空结果、入库、记成功失败」。
- **为什么禁止 `collect()` 里 `return []`**: 空列表和「被限流/页面改版」在热榜场景几乎无法区分，必须抬成失败信号（`EmptyResultError`）。
- **为什么 Collector 自己不写库**: 写库逻辑集中一处，才谈得上统一幂等、统一 raw 落盘、统一失败记录。

### `collectors/dummy.py` · `DummyCollector`

- **目的**: 不访问网络也能验收全链路与幂等。
- **为什么先写假的**: 真实源有网络/反爬噪声；地基验收应只测「你的管道」，不测「对方网站今天心情如何」。

### `main.py` · CLI 子命令

- **目的**: 统一入口，让调度器（Lab 6）和人手调试走同一条路。
- **为什么 Lab 0 就留 `render` / `push` 空壳**: 验收要求 `--help` 能列出四个命令；占位比事后改 CLI 形状更稳。

## 本地怎么验收

```bash
uv run main.py collect --only dummy
uv run main.py stats          # 1 items, 1 new, 0 dup
uv run main.py collect --only dummy
uv run main.py stats          # 1 items, 0 new, 1 dup
uv run python -m tests.test_all
```

## 留给下一 Lab 的接口

- 新渔网 = 新的 `BaseCollector` 子类，交给 `run_collector` 即可。
- `rank_snapshots` + `newly_entered` / `fast_rising` 已在 Store，Lab 1 直接用。
- CLI 的 `collect` 将从「手写字典」演进到 `registry`（Lab 1 完成）。
