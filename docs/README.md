# Fishnet 设计笔记（docs）

这里不是 API 手册，而是**「为什么这样实现」**的笔记。

每完成一个 Lab，按同一模板补一篇文档：先用几句话说明本 Lab 交付了什么功能，再按模块/函数写目的、取舍与踩坑。后面的 Lab 2–9 也照这个格式写即可。

## 已完成

| 文档 | Lab | 一句话 |
|---|---|---|
| [lab-00-foundation.md](./lab-00-foundation.md) | Lab 0 · 地基 | 数据契约、幂等入库、CLI、假采集器跑通全链路 |
| [lab-01-hotlist.md](./lab-01-hotlist.md) | Lab 1 · 热榜聚合 | DailyHotApi 多榜采集、新上榜/蹿升、产出 hotlist.md |
| [lab-02-trendradar.md](./lab-02-trendradar.md) | Lab 2 · TrendRadar | 关键词 DSL、ADR-001；文末含 2.2 精读笔记 |
| [lab-03-rsshub.md](./lab-03-rsshub.md) | Lab 3 · RSSHub | 自部署、订阅、RSSCollector、WeWe RSS 部署指南（3.4） |
| [lab-04-mediacrawler.md](./lab-04-mediacrawler.md) | Lab 4 · MediaCrawler | 子进程隔离、频率/并发、jsonl → Item |
| [lab-05-extract.md](./lab-05-extract.md) | Lab 5 · 正文抽取 | 站点路由 + 见闻 API + robots 个人 override、缓存、质量/RSS 降级 |
| [lab-06-scheduler.md](./lab-06-scheduler.md) | Lab 6 · 调度 | APScheduler 常驻、早晚出报、used_in、系统体检 |
| [lab-07-ranking.md](./lab-07-ranking.md) | Lab 7 · 个性化 | 黄金集、两阶段打分、事件折叠、反馈、A/B |
| [lab-08-render.md](./lab-08-render.md) | Lab 8 · 渲染 | A3 矩阵矩形装箱,digest.md → HTML/PDF |
| [adr/001-why-not-trendradar.md](./adr/001-why-not-trendradar.md) | ADR-001 | 只借鉴 TrendRadar 设计、不依赖其运行时 |
| [adr/002-wechat-mp-strategy.md](./adr/002-wechat-mp-strategy.md) | ADR-002 | 公众号走 WeWe RSS，Fishnet 只消费 RSS |
| [adr/003-mediacrawler-scope.md](./adr/003-mediacrawler-scope.md) | ADR-003 | MediaCrawler 只覆盖指定小红书创作者 |
| [adr/004-extract-and-robots.md](./adr/004-extract-and-robots.md) | ADR-004 | 正文优先 RSS/API;robots 只放行订阅单篇;热榜不爬回答 |
| [adr/005-scheduler-runtime.md](./adr/005-scheduler-runtime.md) | ADR-005 | APScheduler 在家跑;残缺出报;Actions 只做心跳 |
| [adr/006-embed-backend.md](./adr/006-embed-backend.md) | ADR-006 | TF-IDF+SQLite 存向量;不上 chromadb |
| [adr/008-bilibili-transcript-whitelist.md](./adr/008-bilibili-transcript-whitelist.md) | ADR-008 | B 站 UP/合集白名单；合集滴灌进口播栏 |
| [notes/anti-crawling.md](./notes/anti-crawling.md) | Lab 4.2 | 登录态、签名、Playwright 代价、限速 |

## 后续 Lab 怎么写（模板）

新建 `docs/lab-XX-简短英文名.md`，建议结构：

```markdown
# Lab X · 标题

## 本 Lab 完成了什么
- 3～6 条功能交付（用户能跑什么命令、得到什么产物）

## 对应 Lab 原则 / 验收点
- 对照手册里的原则或验收标准，写实现落点

## 模块与函数设计笔记
### `path/to/file.py` · `func_or_class`
- **目的**: …
- **为什么这样写**: …
- **刻意不做的事**: …
- **踩坑 / 边界**: …

## 本地怎么验收
```bash
...
```

## 留给下一 Lab 的接口
- 下一 Lab 会接在哪些函数/表上
```

写的时候优先记「决策」和「代价」，少贴大段代码（代码以仓库为准）。
