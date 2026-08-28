# Lab 3 · RSSHub 订阅版面

> **范围**: 3.1–3.4（自部署、订阅清单、RSS Collector、公众号 WeWe RSS 方案）。  
> **3.5 自写 RSSHub 路由**: 本轮不做（已从仓库移除）。

## 本 Lab 完成了什么

1. **自部署 RSSHub**:`docker-compose.yml` + Redis，官方 `diygod/rsshub` 镜像。
2. **订阅清单 ≥10 源**:`config/sources.yaml` 的 `feeds:` 加上 `wechat.yaml` 公众号（不含个人 B 站 UP、「B站每周必看」）。
3. **通用 `RSSCollector`**:`collectors/rss_generic.py`。
4. **公众号 3.4**:[ADR-002](./adr/002-wechat-mp-strategy.md) + `docker-compose.wewe-rss.yml` + 下文部署步骤。
5. **版面**:`render/sections/subscriptions.md`；测试 `tests/test_lab3.py`。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 自建 RSSHub 跑通 | `docker compose up -d` |
| ≥10 订阅源 | `sources.yaml` feeds |
| 知乎 + 新番 + 财经 | Thoughts Memo/差评君；知乎日报早报；Bangumi 今日放送；华尔街日报/见闻。不定个人 B 站 UP |
| 公众号方案 | ADR-002 + WeWe RSS compose 文件 |
| subscriptions.md | `render/subscriptions.py` |

## 3.4 部署 WeWe RSS（手把手）

WeWe RSS 把「微信公众号」变成标准 RSS/Atom，Fishnet 只消费 feed URL，不碰微信登录。

### 第一步：准备授权码

在 `fishnet-reference` 目录：

```bash
cp .env.example .env
# 编辑 .env，设一个你自己记得的字符串，例如:
# WEWE_AUTH_CODE=你的随机口令
```

`AUTH_CODE` 用于保护 WeWe RSS 管理接口，**不要提交进 Git**（`.env` 已在 `.gitignore`）。

### 第二步：启动容器

```bash
cd fishnet-reference
docker compose -f docker-compose.yml -f docker-compose.wewe-rss.yml up -d
```

浏览器打开 **http://127.0.0.1:4000**。

### 第三步：添加微信读书账号

1. 进入「账号管理」→「添加账号」
2. 用微信扫二维码登录 **微信读书**
3. **不要勾选**「24 小时后自动退出」（否则要频繁重登）

若账号显示「今日小黑屋」：添加公众号太频繁被封控，等 **24 小时** 或重启容器清记录（账号正常时）。

### 第四步：订阅公众号

1. 进入「公众号源」→「添加」
2. 提交该号的 **微信公众号分享链接**（从微信里复制文章链接或分享链接）
3. 添加成功后，界面会给出该号的 **feed 地址**，形如：
   - `http://127.0.0.1:4000/feeds/MP_WXS_xxxxx.rss`
   - 或 `.atom` / `.json`

**注意**: 短时间大量添加容易被封控，建议先加 1–2 个号试跑。

### 第五步：接入 Fishnet

```bash
cp config/wechat.yaml.example config/wechat.yaml
```

编辑 `config/wechat.yaml`（此文件已 gitignore，可写真实 feed URL）：

```yaml
feeds:
  - name: "你想显示的公众号名"
    url: "http://127.0.0.1:4000/feeds/MP_WXS_xxxxx.rss"
    source: wechat_mp
    kind: article
    weight: 2.0
    # title_exclude_regex: 标题命中则当广告丢掉。差评每日两槽用 今日最佳|聊一聊
```

出报时另有一条全局规则:标题字数大于正文字数也当广告丢掉(征稿/软广常把整段话写进标题,库里几乎没有正文)。采集仍入库,抽完正文后再判;不进订阅版,也不进个性化打分。

`core/settings.py` 的 `load_feeds()` 会自动合并 `sources.yaml` 与 `wechat.yaml`。

采集与渲染：

```bash
uv run main.py collect --only-rss
uv run main.py stats          # 应看到 wechat_mp 计数增加
uv run main.py render --section subscriptions
```

### 常用运维

| 操作 | 命令 / 说明 |
|---|---|
| 看日志 | `docker compose -f docker-compose.yml -f docker-compose.wewe-rss.yml logs -f wewe-rss` |
| 停服务 | `docker compose -f docker-compose.yml -f docker-compose.wewe-rss.yml down` |
| 手动刷新某 feed | 浏览器访问 `http://127.0.0.1:4000/feeds/MP_WXS_xxx.rss?update=true` |
| 全文模式 | 在 `docker-compose.wewe-rss.yml` 里设 `FEED_MODE: fulltext`（更慢） |

架构决策详见 [ADR-002](./adr/002-wechat-mp-strategy.md)。

## 知乎订阅(机构号 / 个人号)

- **指定作者**:只订 `zhihu/people/answers/{id}`(Thoughts Memo、差评君)。不订 `activities`,避免把「赞同了回答」灌进报纸。
- **知乎日报机构号**: `zhihu/posts/org/zhi-hu-ri-bao-51-41`,主页 `https://www.zhihu.com/org/zhi-hu-ri-bao-51-41`。配置了 `title_regex: 早报`(匹配标题里的「｜早报 YYYYMMDD」,以及正文「嘿，这里是知乎早报」),「瞎扯」等其它帖丢掉。见报时标题印「今日知乎日报」,原来的长目录加粗放在正文第一段;当天早报钉进深度阅读,不靠把 `weight` 调很大。
- **知乎周刊**已停更,不再订 `/zhihu/weekly`。
- **热榜问题**仍走 DailyHot,不在 RSS 里扒回答;以后点名再拉高赞回答。
- 知乎 RSSHub 路由常 403/503:在 `.env` 填 `ZHIHU_COOKIES`(见 `.env.example`),然后 `docker compose up -d --force-recreate rsshub`(只改 `.env` 不重建,容器里仍是空 Cookie)。Cookie 只给 RSSHub 容器,不要提交。正文边界见 [ADR-004](./adr/004-extract-and-robots.md)。

## 本地怎么验收（RSS 订阅部分）

```bash
docker compose up -d
uv run python -m tests.test_lab3
uv run main.py collect --only-rss
uv run main.py render --section subscriptions
```

## 留给下一 Lab 的接口

- 公众号 Item 的 `source=wechat_mp`，Lab 7 打分应对深度版面设 $w_{\text{hot}}=0$。
- 有 `content` 的 feed 可在 Lab 5 跳过正文抽取；否则按 URL 抽取。

## 思考题备忘

1. **RSS 窗口漏抓**: 调 `interval_minutes` 与 feed 长度。
2. **RSSHub vs 正文抽取**: 发现 vs 内容。
3. **RSSHub 全挂**: 订阅版面受损；财经等可走官方 RSS 旁路。
