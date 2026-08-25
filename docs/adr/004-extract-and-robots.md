# ADR-004 · 正文从哪来、何时覆盖 robots

- **状态**: 已接受
- **日期**: 2026-08-25
- **背景**: Lab 5。本地 20 页 HTML 验收通过后,直播 `enrich --limit 20` 一度 `ok=0`(微信 robots 全拦、华尔街见闻 SPA 空壳)。知乎热榜/日报/专栏的需求随后叠上来,必须把「发现」和「正文」拆开,并写死合规边界。

## 决策

1. **发现走订阅/热榜,正文另一步。** DailyHot 与 RSSHub 只负责「有哪些条目」;缺 `items.content` 时才 `enrich`。报纸(Lab 8)仍只渲染摘要 + 链接,SQLite 里的全文供检索和打分(`content or summary`)。
2. **能白嫖就不抓 HTML。** RSS `content:encoded` / 足够长的 summary、华尔街见闻公开 JSON API(`llms.txt` 允许引用)、知乎日报 feed 描述里的早报全文,优先于再打原站。
3. **默认遵守 robots.txt;个人订阅只放行单篇路径。** 不把整站 override 写进配置。当前允许:
   - `mp.weixin.qq.com/s…`(WeWe 源经常无正文,见 [ADR-002](./002-wechat-mp-strategy.md))
   - `zhuanlan.zhihu.com/p/…`
   - `www.zhihu.com/question/…/answer/…`(指定作者的回答)
4. **知乎热榜问题不爬回答。** 热榜只保留标题 + 问题链接。以后若要读回答:先点名,再拉部分高赞,不在 enrich 里对整张榜无差别抓取。
5. **知乎日报只收早报。** 机构号 `zhi-hu-ri-bao-51-41` 走 `zhihu/posts/org/…`,`title_regex` 匹配「早报」(含正文「嘿，这里是知乎早报」)。「瞎扯」等帖丢掉。知乎周刊已停更,不再订。
6. **Cookie 只给 RSSHub,不进抽取器。** `ZHIHU_COOKIES` 在 `.env`,compose 注入 rsshub 容器;改完必须 `docker compose up -d --force-recreate rsshub`。`PoliteFetcher` 不带知乎登录态。B 站/抖音热榜没有网页正文,`missing` 高是预期,不为此放宽质量门。

## 明确不做

- 不把 Playwright / MediaCrawler 用于知乎、微信、见闻正文([ADR-003](./003-mediacrawler-scope.md))。
- 不 override `www.zhihu.com/question/{id}`(无 `/answer/` 的热榜问题页)。
- 不把 `items.content` 整篇塞进 PDF。
- 不在 git 里存 Cookie / `tmp/` 抽查稿。

## 后果

- **正面**: 微信/见闻/日报早报在个人自用范围内能进库;热榜仍轻;失败可降级为标题 + summary。
- **负面**: 专栏 HTML 无 Cookie 时仍可能是登录壳;RSSHub 知乎路由绑定登录态,Cookie 过期要手换;见闻会员稿可能先被 500 字摘要占住 `content`,不会自动重抽。
- **合规**: 自用、低频、已订阅信源、单篇而非搜索/列表;覆盖 robots 仅限上列路径。

## 何时重新评估

1. 微信/知乎提供官方正文 API 或稳定全文 RSS → 关掉对应 override。
2. 真的需要「点名热问的高赞回答」→ 另开受限入口(RSSHub `/zhihu/question/{id}` + Cookie + 条数上限),不改热榜 enrich 默认行为。
3. robots / ToS 收紧到个人单篇也不允许 → 该源只保留标题 + 链接。
