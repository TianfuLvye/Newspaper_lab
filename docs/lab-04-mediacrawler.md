# Lab 4 · MediaCrawler:面对真实的反爬

> **范围**: 装起来、看懂反爬、子进程接入、写清「要不要它」。  
> **直播扫码**: 本机运维步骤;CI / Agent 用 jsonl fixture 验收 Item 转换与入库。

## 本 Lab 完成了什么

1. **进程隔离**: `collectors/targeted_xhs.py` 用 `subprocess` 调 MediaCrawler,主进程只读 jsonl。
2. **频率与并发写死**: `interval_minutes = 360`,`max_concurrency = 1`;并发 >1 直接拒绝。
3. **默认 collect 不带它**: Playwright 太重,只有 `--only-targeted` / `--only xhs_*` 才跑。
4. **笔记**: `docs/notes/anti-crawling.md` 回答 4.2 四个问题。
5. **ADR-003**: MediaCrawler 只覆盖指定小红书创作者;搜索式采集不做。
6. **测试**: `uv run python -m tests.test_lab4`(fixture → Item → SQLite,不启动浏览器)。

## 对应验收点

| 验收 | 落点 |
|---|---|
| 抓 1 个指定创作者并转成 Item 入库 | fixture 路径已通;`collect --only-targeted` 需本机扫码后写 `creator_id` |
| `docs/notes/anti-crawling.md` 答完四问 | 登录态 / 签名 / Playwright 代价 / 限速 |
| 频率 ≥ 6 小时,并发 = 1 | 类属性 + 构造期校验 |
| ADR 写清定位与边界 | ADR-003(ADR-002 已用于公众号) |

## 模块与函数设计笔记

### `collectors/targeted_xhs.py` · `XHSCreatorCollector`

- **目的**: 把「指定小红书创作者」收成普通 Collector,输出仍是 `Item`。
- **为什么子进程**: MediaCrawler 依赖 Playwright/Chromium,import 进主进程会把内存、启动时间和浏览器故障传给热榜/RSS。
- **为什么可注入 `jsonl_path`**: 单测和「已经抓过一次」的复跑不该再开浏览器。契约不变:有数据就 `yield Item`,没有就抛错。
- **刻意不做**: 不在主进程里调 Playwright、不实现搜索、不爬评论、不下载视频。

### `row_to_item`

- **目的**: 兼容 MediaCrawler 落库字段(`note_url`,`liked_count`,`nickname`)和原始 note(`user`,`interact_info`)。
- **踩坑**: 教学版可能把 `user_id` 收成 `creator_hash`;两套都认,避免上游一脱敏我们的 `author_id` 就空。

### `core/registry.py`

- **目的**: `include_targeted=False` 为默认,避免 `collect` 误触发扫码窗口。
- **查找**: `get_collector("xhs_...")` 仍能找到,方便 `--only`。

## 本地怎么验收

```bash
uv run python -m tests.test_lab4

# 直播抓取(需要本机扫码,任选):
git clone --depth 1 https://github.com/NanmiCoder/MediaCrawler third_party/MediaCrawler
cd third_party/MediaCrawler && uv sync
# 上游默认 CDP:Chrome 地址栏打开 chrome://inspect/#remote-debugging 并允许;
# 或不想用 CDP:把 config/base_config.py 的 ENABLE_CDP_MODE 改为 False,再 playwright install
# 编辑 config/sources.yaml targeted.creator_id
uv run main.py collect --only-targeted
uv run main.py stats    # 应出现 xiaohongshu
```

## 留给下一 Lab 的接口

- Item 已带 `source=xiaohongshu` / `kind=post`;Lab 5 抽取正文时优先用已有 `summary`,缺 `content` 再打原页。
- Lab 7 打分:定向帖应 `w_hot` 很低或为 0,兴趣分来自作者与关键词,不来自赞数崇拜。
- Lab 6 调度:挂 APScheduler 时用 `interval_minutes=360`,不要跟热榜 30 分钟混在一个 tick 里。

## 思考题备忘

1. **订阅 vs 搜索**: 订阅是 O(订阅数)、像正常人;搜索是 O(词 × 页)、像爬虫。风险/成本/质量三个维度订阅全面占优,搜索只在「目标未知」时才值得。
2. **robots / ToS / 法律**: 惯例 < 合同 < 强制法。个人自用与对外服务的界线是分发、规模、牟利、市场替代四条,任一越线性质就变。
3. **平台禁止抓取时**: 官方 API → 官方 RSS → 邮件订阅 → 手动投喂链接 → 换信源 → **放弃该版面**。放弃是合格选项。
