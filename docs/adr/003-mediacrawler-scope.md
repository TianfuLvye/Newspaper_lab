# ADR-003 · MediaCrawler 在 Fishnet 里的定位与边界

- **状态**: 已接受
- **日期**: 2026-08-23
- **背景**: Lab 4。手册原文写「写进 ADR-002」,但 ADR-002 已用于公众号 WeWe RSS,故本决策单列为 ADR-003。

## 决策

**MediaCrawler 只负责 RSSHub 覆盖不了的 1 个定向版面:指定小红书创作者。** 不进默认 `collect`,不用于关键词搜索,不用于知乎/B 站/微博(那些走订阅)。

## 愿景对照:哪些需求「只能」靠它?

| 愿景 | 是否必须 MediaCrawler | 理由 |
|---|---|---|
| 指定公众号 | 否 | ADR-002:WeWe RSS → `RSSCollector` |
| 指定 B 站 UP / 新番 | 否 | RSSHub 已通 |
| 指定知乎作者 | 否 | RSSHub;个人页 403 时补 Cookie,不是换爬虫 |
| 知乎「优质内容」 | 否 | 订阅 + 自己的排序,比搜索式抓取更合适 |
| 热榜 | 否 | DailyHotApi |
| **指定小红书创作者** | **是** | 无公开 RSS,必须登录态 + 浏览器签名 |
| 指定抖音创作者 | 预留,暂不接 | 同理由,但本轮只做 1 个创作者验收 |
| 关键词全网搜索 | **明确不做** | 请求量爆炸、像爬虫、封号风险高 |

结论与 Lab 手册一致:**它在系统里是低优先级的小众版面,禁止拖累整体进度。**

## 架构落点

```
config/sources.yaml  targeted:
    → XHSCreatorCollector(interval_minutes=360, max_concurrency=1)
    → subprocess: uv run main.py --platform xhs --type creator ...
    → data/mc_out/*.jsonl
    → Item(source=xiaohongshu, kind=post)
    → Store.upsert_items
```

- Fishnet **不 import** MediaCrawler。
- 默认 `uv run main.py collect` **不会**启动 Playwright。
- 显式入口:`uv run main.py collect --only-targeted` 或 `--only xhs_小红书示例创作者`。

## 合规边界(许可 + 法律通识,非律师意见)

MediaCrawler 许可证限制学习/研究、禁止大规模抓取与商用。本仓库的落法:

- 并发 = 1,间隔 ≥ 6 小时,`max_notes` ≤ 10
- 只抓你自己订阅的创作者,不做关键词翻页
- 产出只进个人 SQLite / 本地 Markdown,不再分发全文
- Cookie / 浏览器档案留在 `third_party/MediaCrawler`,gitignore

越线的例子:做成公开网站、高并发、把笔记当「替代原站阅读」的产品。

## 后果

- **正面**: 主进程保持轻量;失败隔离(子进程挂了不影响热榜/RSS);边界写死,面试时能讲清。
- **负面**: 小红书版面依赖本机扫码;CDP/Chrome 环境脆弱;平台改签名时要跟上游,不自己养逆向。
- **不做**: 代理池、多账号、搜索式采集、把 MediaCrawler 当核心管道。

## 何时重新评估

1. 小红书提供官方创作者 RSS / API
2. 许可证或平台条款收紧到「个人自用也不允许」→ 放弃该版面(Lab 4 思考题 3 的合法选项)
3. 你真正订阅的 XHS 创作者 >3 且稳定性成为瓶颈 → 先考虑减少订阅,而不是加并发
