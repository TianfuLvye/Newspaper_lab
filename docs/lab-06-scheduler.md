# Lab 6 · 调度与可靠性:让渔网真的挂在海里

> **范围**: APScheduler 常驻、采集安全壳、早晚出报、`used_in`、报纸最后一页系统体检。  
> **决策**: 调度跑在哪、残缺出报见 [ADR-005](./adr/005-scheduler-runtime.md)。

## 本 Lab 完成了什么

1. **`scheduler/run.py`**: `BlockingScheduler`(Asia/Shanghai)。热榜按 `interval_minutes=30`、RSS=60、定向=360;正文抽取每 6 小时;每天 07:00 早报、18:00 晚报。
2. **jitter=120 + coalesce + max_instances=1**: 打散整点齐发;错过的任务只补一次。
3. **`pipeline/edition.py`**: `produce_edition("am"|"pm")` 各版面独立生成。订阅版按源轮询、印正文;每篇另写 `items/*.md` 供离线打开。
4. **`used_in`**: 上了早报的 hash 标成 `YYYY-MM-DD-am`,晚报候选自动跳过。
5. **`pipeline/health.py` + `render/health.py`**: **跑过但全失败** 与 **24h 没调度** 分开报,避免没开 `serve` 时满页假警报。
6. **CLI**: `uv run main.py serve` / `render --edition am` / `health`;每个子命令可单独跑,不必等 cron。
7. **测试**: `uv run python -m tests.test_lab6`(不启动常驻进程、不依赖外网)。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 常驻 + RSSHub 挂了仍能出报 | `_job_collect` / `run_collector` 永不向上抛;版面 try/except + 占位 |
| 早晚 pipeline,`used_in` 不重复 | `produce_edition` + `Store.mark_used`;订阅/热榜 `unused_only=True` |
| 体检页报出制造的故障 | `diagnose(expected=...)`;失败写进 digest / `99_health.md` |
| 子命令独立可跑 | `collect` / `stats` / `render` / `enrich` / `health` / `serve` / `push` |
| 失败隔离 | Lab 0 的 `run_collector` 安全壳;出报层再包一层 |

72 小时不崩是运行时验收:本机 `uv run main.py serve`,期间 `docker stop` 掉 rsshub,再 `render --edition am` 应仍写出 digest,体检页点名失败的 RSS 网。

## 模块与函数设计笔记

### `scheduler/run.py` · `build_scheduler`

- **目的**: 代码内配置两种节奏,测试只检查 job,不 `start()`。
- **为什么 APScheduler 而不是 cron**: 单机 Python 原生;和 `main.py` 同一条路,手动 `render --edition am` 能复现明早将发生的事。
- **为什么传 collector 名而不是实例**: 线程池里每次 job 自己 `Store(db)` + `get_collector(name)`。SQLite 连接不能跨线程共用。
- **为什么 targeted 没填 `creator_id` 不挂**: 否则每 6 小时记一条「未配置」失败,体检永远红。填了 id 才会 `enabled=True`。
- **刻意不做**: 不出 07:30 PDF 推送(Lab 9);不把 dummy 进常驻。

### `jitter` / `coalesce`

- **jitter**: 没有它,N 张网在整点同时打 DailyHot / RSSHub,像一次小 DDoS,也更容易撞缓存窗口。
- **coalesce**: 笔记本合盖一晚,醒来只补跑一次,不把错过的 16 个 tick 堆成雪崩。
- **max_instances=1**: 上一轮还没结束(正文抽取、MediaCrawler)时,不叠跑。

### `pipeline/edition.py` · `produce_edition`

- **目的**: 「收网」。07:00 / 18:00 以及手动 `--edition` 走同一函数。
- **deadline**: 总时限默认 20 分钟。早报的价值 90% 在准点送到;超时版面改占位,体检页尽量保留。
- **为什么成功后才 `mark_used`**: 版面失败时条目仍保持未使用,晚报或下次手动还能再出。
- **调试 `render --section hotlist` 不标记**: 方便反复渲染;只有 `--edition` 才消费 `used_in`。

### `pipeline/health.py` · `diagnose`

- **24h 从未成功**: 在预期名单里且 `ok_runs=0`(含根本没跑过)。这是「网破了」而不是「今天世界很平静」。
- **产出骤降**: 近 24h `new_count` < 20% × (7 日总量 / 7)。日均 < 3 条不告警,避免冷启动误报。
- **为什么印在报纸上**: 写进日志你不会看;印在最后一页,每天扫一眼就完成巡检。

### `core/store.py` · `health` / `db_size_bytes` / `unused_age`

- Lab 0 已预留 `collector_runs` 和 `used_in`。本 Lab 补上 7 日对比和磁盘水位,不另起一套表。

## 本地怎么验收

```bash
uv run python -m tests.test_lab6
uv run python -m tests.test_all          # 失败隔离 / used_in 回归

# 手动出一期(不必等到明天早上)
uv run main.py render --edition am
uv run main.py health
ls data/editions/

# 常驻(另开终端)
uv run main.py serve
# 另开: docker stop <rsshub容器> 后再出报,体检页应点名失败的 rss_* 
```

## 思考题备忘

1. **跑在哪**: 主力采集放家里(树莓派 / 旧笔记本 / NAS),要的是住宅 IP 和持久 Cookie。云主机便宜但机房 IP 易被当爬虫;GitHub Actions 适合当心跳告警,不适合当爬虫运行时。见 ADR-005。
2. **7 点 pipeline 崩了**: 超时熔断 + 残缺出报,报头写清缺了哪些版。不出报等于产品死亡;死等完整报会错过早餐窗口。
3. **Actions 额度**: 公开仓库分钟数够,但 IP 公开、无状态、cron 会飘、ToS 不欢迎持续爬取。把它当「6 小时没心跳就告警」的备份通道。

## 留给下一 Lab 的接口

- `data/editions/{YYYY-MM-DD-am}/digest.md` 是 Lab 8 的 Markdown 中间层入口;PDF 从这里走,不要从 collector 直出。
- 体检页文件名 `99_health.md`,Lab 8 模板目录按这个序号接。
- `used_in` 已有值的条目不再进候选;Lab 7 打分只看未使用 + 当日窗口(已接入 `produce_edition`)。
- 出报 Markdown 已含正文(或视频简介),Lab 8 排版时直接吃 `digest.md` / `items/*.md`,不要再做成标题链接表。
- 推送(07:30 / 18:30)仍是 `main.py push` 占位,Lab 9 接 SMTP / Telegram。
