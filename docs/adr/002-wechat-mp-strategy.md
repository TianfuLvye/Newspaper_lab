# ADR-002 · 微信公众号订阅方案

- **状态**: 已接受；**已接入 1 个号**（章北海的自然选择）
- **日期**: 2026-08-09（接入 2026-08-23）
- **背景**: Lab 3.4 —— 公众号是 Fishnet 愿景里「指定信源」的重要一块，但微信没有公开 RSS/订阅 API。

## 决策

**不把公众号抓取写进 Fishnet 主仓库**；采用 **外部 RSS 中转服务 → RSSHub/直连 URL → `RSSCollector`** 的标准订阅链路。

首选自建 **[WeWe RSS](https://github.com/cooderl/wewe-rss)**（微信读书接口中转），备选商业 **wechat2rss**；**不采用**搜狗微信搜索爬虫，**不采用**新榜 Cookie 路由作为长期方案。

## 方案对比（Lab 手册 3.4 表）

| 方案 | 结论 | 理由 |
|---|---|---|
| 商业 wechat2rss | 备选 | 省事但要付费；账号少时 acceptable |
| **WeWe RSS 自建** | **首选** | 开源、可自部署、输出标准 RSS；维护成本可控 |
| 搜狗微信搜索 | 拒绝 | 反爬强、内容不全、易失效 |
| 手动 + 半自动 | MVP 兜底 | 转发链接到「稍后读」入口，零合规风险 |
| RSSHub `/newrank/wechat/:wxid` | 不选为默认 | 强依赖 `NEWRANK_COOKIE`（第三方付费），与「自部署可控」目标冲突 |

## 架构落点

```
微信公众号
    → WeWe RSS（独立容器，需微信读书登录态）
    → http://wewe-rss:4000/feed/...  （Atom/RSS）
    → Fishnet RSSCollector（已有）
    → items 表（source=wechat_mp）
    → subscriptions.md / 后续打分（w_hot=0）
```

Fishnet 侧**只消费 RSS**，不碰微信登录、不存 Cookie（Cookie 留在 WeWe RSS 容器 / `.env`）。

## 当前状态（已知限制）

- **已接入**: `config/wechat.yaml` 中的「章北海的自然选择」,`collect --only-rss` 能写入 `source=wechat_mp`。
- 其它号可按同样方式加 feed URL;短时间批量加号可能触发微信读书「小黑屋」。
- WeWe RSS 登录态在独立容器里,Fishnet 仍只消费 RSS。

## 配置约定

- 模板: `config/wechat.yaml.example` → 复制为 `config/wechat.yaml`（已在 `.gitignore`）
- 每条 feed: `name`, `url`, `source: wechat_mp`, `kind: article`
- URL 优先走 WeWe RSS 输出的 `/feed/{id}`，不要写 `mp.weixin.qq.com` 直链（会过期）

## 后果

- **正面**: 采集与微信反爬解耦；Fishnet 主进程无 Playwright/微信依赖；符合 Lab 0「失败隔离」。
- **负面**: 多一个要维护的容器；微信读书接口变更时需跟进 WeWe RSS 上游。
- **合规**: 自用、低频、不再分发；PDF 只放摘要+链接（见 Lab 5 Answers Q2）。

## 何时重新评估

1. 微信开放官方订阅 API（极低概率）
2. WeWe RSS 长期不可用 → 切 wechat2rss 或纯手动
3. 订阅号 >10 且稳定性成瓶颈 → 评估商业 RSS 托管
