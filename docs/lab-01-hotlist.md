# Lab 1 · 热榜聚合

## 本 Lab 完成了什么

用 DailyHotApi 拿到「第一桶真鱼」，并做出报纸的第一个真实版面片段：

1. **自部署依赖假设**: 默认打本机 `http://127.0.0.1:6688`（`config/settings.toml`），不绑死公共实例。
2. **一张网适配多榜**: `HotlistCollector(board, source)`，在 `config/sources.yaml` 声明 weibo / zhihu / bilibili / douyin / toutiao，并加 `thepaper` 作稳定性兜底。
3. **增量检测**: 用 `rank_snapshots` 实现「过去 6 小时新上榜」与「排名蹿升」。
4. **第一个 Markdown 产物**: `uv run main.py render --section hotlist` → `render/sections/hotlist.md`（今日新上榜 Top 20）。
5. **注册表**: `core/registry.py` 读配置实例化采集器，`main.py collect` 默认跑全部热榜。
6. **验收测试**: `tests/test_lab1.py`（逻辑）+ `tests/test_lab1_endurance.py`（连跑稳定性；开发用 3 分钟冒烟，正式验收用 6 小时）。

对应愿景：微博 / B 站 / 知乎等热榜速览进报纸，且强调「新上榜 / 蹿升」，而不是每次复读整张榜。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| ≥5 平台稳定入库、连跑不崩溃 | `sources.yaml` 多榜 + `run_collector` 失败隔离 + endurance 脚本 |
| `newly_entered()` 正确 | `Store.newly_entered`：窗口内出现过且窗口前从未出现 |
| 分源计数合理 | `main.py stats` / `SELECT source, count(*) …` |
| 产出 hotlist.md | `render/hotlist.py` → `render/sections/hotlist.md` |
| 采集与加工解耦 | Collector 只 yield Item；「上不上 Top20」在 render 层决定 |
| 原始 payload 落盘 | 每条热榜 row 进 `Item.raw` |

## 模块与函数设计笔记

### `config/sources.yaml` + `core/settings.py`

- **目的**: 把「开哪几张网、API 基址」从代码里拔出来。加榜 = 改 YAML，不必改 Python。
- **为什么自部署 URL 写进 settings**: Lab 明确说公共实例会挂；配置化方便以后换端口/机器，而不改 collector。
- **为什么多挂 thepaper**: 实测 `weibo` 上游常 500。验收要「至少 5 平台」，用额外一张稳榜做工程兜底，而不是假装微博永远健康。

### `collectors/hotlist_generic.py` · `HotlistCollector`

- **目的**: **一个类适配 N 个榜单**——抽象层次在「热榜 API 形态」，不在「微博特殊逻辑」。
- **为什么 `__init__(board, source)`**: DailyHot 路径名（`/weibo`）和业务 `Source` 枚举不必同名（如 `toutiao` → `news`）。
- **为什么 `collect()` 里不写库**: 遵守 Lab 0 契约；快照由 `run_collector` 在 `c.board` 有值时统一 `record_snapshot`。
- **刻意不做**: 关键词过滤、个性化打分、决定版面——那些是 Lab 2 / 7 的事。热榜网的任务只是「把榜捞进 Raw Store」。

### `hotlist_generic.py` · `_to_float` / `_parse_ts`

- **目的**: 把各平台乱七八糟的热度字符串、时间戳收成可排序/可比较的类型。
- **为什么热度要认「万/亿」**: 不转换就无法跨条目比较，Top20 排序会失真。
- **为什么时间戳要拒绝离谱值**: 头条等源会吐出超大数字；`datetime.fromtimestamp` 直接炸进程。解析失败应返回 `None`，让采集继续——**单字段脏数据不能杀死整张网**（失败隔离在字段级的延伸）。
- **为什么请求重试 1 次**: DailyHot 偶发抖动；重试便宜。但微博持续性 500 不靠重试解决，靠多榜冗余。

### `core/registry.py`

- **目的**: 调度器 / CLI 不用手写 `from collectors.xxx import …` 长名单。
- **为什么现在用「读 YAML 实例化」而不是复杂插件发现**: Lab 1 只有热榜一类；够用且可读。以后 RSS/定向采集变多，再加强 `pkgutil` 扫描也不迟。
- **`dummy` 默认不进全量 collect**: 避免污染真实热榜统计；需要时用 `--include-dummy` 或 `--only dummy`。

### `core/store.py` · `newly_entered`

- **目的**: 回答「什么是新上榜的」，而不是「现在榜上有什么」。
- **定义**: 在时间窗 \(W\) 内出现过，且在 \(W\) 之前该 board 上从未出现过。
- **为什么需要快照表**: 只看 `items` 分不清「刚进榜」和「一直在榜」；身份表没有历史。
- **冷启动注意**: 第一次采样时，窗口前没有历史，窗内全部会被当成「新上榜」——这是预期行为，不是 bug。要稳定「增量感」，至少需要跨两次采样。

### `core/store.py` · `fast_rising`

- **目的**: 抓「蹿升」而不是「当前最高」。
- **公式**: \(\Delta r = r_{\text{first}} - \min_{t \in W} r_t\)（首次名次 − 窗口内最佳名次）。
- **为什么用最佳名次而不是最新名次**: 很多爆点是冲上去又掉下来；用最新名次会错过它们，而这类内容往往最值得进报。

### `core/store.py` · `get_item`

- **目的**: 增量检测返回的是 hash，渲染层需要完整 `Item`（标题、链接、热度）。
- **为什么不让 `newly_entered` 直接返回 Item**: Store 查询保持轻量；拼装展示是 render 的职责，边界更清晰。

### `render/hotlist.py`

- **目的**: Lab 1 验收要求的第一个报纸碎片——「今日新上榜 Top 20」。
- **为什么单独模块而不是塞进 `main.py`**: CLI 只负责参数与调用；渲染逻辑要可单测、可被 endurance 每轮复用。
- **为什么标注「不代表重要性」**: 热榜是幸存者偏差源（Lab 思考题）；版面上先诚实标注，比假装客观更有用。
- **排序策略**: 先按 heat，再按 rank——在个性化打分（Lab 7）到来之前的朴素默认。

### `main.py` · `collect` / `render`（Lab 1 增量）

- **目的**: 人手与脚本都能 `collect` → `stats` → `render` 走通。
- **`--strict`**: 默认关闭（失败隔离：单榜挂了仍退出 0）；CI/验收想「必须全绿」时再打开。
- **`stats` 打印 `by_source`**: 对应验收里那条手动 SQL，省得每次打开 sqlite3。

### `tests/test_lab1.py` / `tests/test_lab1_endurance.py`

- **目的**: 把验收标准变成可重复命令，而不是「我觉得跑起来了」。
- **为什么拆成两个文件**:
  - `test_lab1`: 快、可假数据，测公式与渲染，不依赖「连跑多久」。
  - `test_lab1_endurance`: 真打 API，测崩溃/连续失败；`--minutes 3` 开发冒烟，`--hours 6` 正式验收。
- **为什么 endurance 用独立 db**: `data/fishnet_endurance.db`，避免把日常库打成实验场。
- **成功门槛**: 单轮至少 5 个 collector `status=ok`；连续多轮零成功则失败。微博挂了只要其它五张还在，仍算达标。

## 本地怎么验收

```bash
# 1) API
docker start dailyhot   # 或按 README docker run …

# 2) 采集 + 产物
uv run main.py collect
uv run main.py stats
uv run main.py render --section hotlist
# 打开 render/sections/hotlist.md

# 3) 单测
uv run python -m tests.test_lab1

# 4) 稳定性（任选）
uv run python -m tests.test_lab1_endurance --minutes 3 --interval 60
uv run python -m tests.test_lab1_endurance --hours 6
```

## 留给下一 Lab 的接口

- Lab 2（TrendRadar）: 关键词 DSL 已有 `pipeline/keyword.py`；可对热榜 Item 做 must/any/exclude，但**不要把过滤塞回 Collector**。
- Lab 6: `registry.all_collectors()` + 各网 `interval_minutes` 可直接挂 APScheduler。
- Lab 7: `hotlist.md` 只是朴素 Top20；个性化排序应替换/增强 render 前的候选池，而不是改 DailyHot 请求代码。
- Lab 8: 已有 Markdown 中间层碎片，完整报纸渲染可拼多个 `render/sections/*.md`。

## 个人体会（写给后续的自己）

1. **热榜的价值在「变化」不在「清单」。** 只存当前榜等于每次覆盖；快照表才是 Lab 1 的灵魂。
2. **依赖会说谎。** DailyHot 的 weibo 500、toutiao 离谱 timestamp，都说明：字段级容错 + 多源冗余，和「失败隔离」是同一原则的不同尺度。
3. **配置声明渔网、代码只实现网的形状。** `sources.yaml` 让「加一个平台」变成一行配置——这是后面 60+ 源（NewsNow 那种组织）的最小雏形。
