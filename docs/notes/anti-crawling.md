# 反爬观察笔记 · Lab 4.2

对照 [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 源码(README、`config/base_config.py`、`media_platform/xhs/help.py`、`store/xhs/__init__.py`)。未在本机对小红书做 DevTools 实抓:创作者接口要扫码登录,这是运维步骤,不是采集器契约问题。

## 1. 登录态怎么维持? Cookie 在哪? 过期了怎样?

- **载体**: Playwright 的 `browser_context`,配合 `USER_DATA_DIR = "%s_user_data_dir"` 把浏览器档案落在磁盘(`SAVE_LOGIN_STATE = True`)。下次启动复用同一份 profile,Cookie / localStorage 都在里面。
- **登录方式**: `LOGIN_TYPE` 支持 `qrcode` / `phone` / `cookie`。CLI `--lt qrcode` 是最温和的入门路径。
- **CDP 模式**(默认 `ENABLE_CDP_MODE = True`):连本机已经打开的 Chrome,直接复用你日常浏览的 Cookie 和扩展,风控比「无痕再扫一次」低。
- **过期**: 平台踢登录后,请求会 401/跳登录页;项目不会「静默续命」,需要再扫码或重灌 Cookie。`COOKIES = ""` 是给 cookie 登录预留的注入口,**不要提交进 Git**。
- **教学版额外约束**: 落库时把 `user_id` 收成 `creator_hash`、昵称脱敏(`store/xhs/__init__.py`),降低把真实账号资料带出仓库的风险。

## 2. 请求签名是什么? 项目怎么生成?

小红书 Web 接口除了 Cookie,还要带 `x-s`、`x-t`、`x-s-common`、`x-b3-traceid` 这类头。它们不是你自己 invent 的 HMAC,而是**平台前端 JS 在浏览器里算出来的**。

MediaCrawler 的策略(README 原话):「无需 JS 逆向;利用保留登录态的浏览器上下文,通过 JS 表达式获取签名参数。」也就是:

1. 先有一个已登录的 Chromium/Chrome 上下文;
2. 在页面里 `evaluate` 平台自己的签名函数;
3. `media_platform/xhs/help.py` 的 `sign()` 再把 `a1`(Cookie)、`x_s`/`x_t`(页面算出)、`b1`(localStorage)拼成 `x-s-common`。

所以「那串看不懂的参数」= 浏览器里跑出来的反爬票据。纯 `requests` 拿不到,除非你把那坨 JS 抠出来自己跑——那就是逆向,也是项目刻意避开的路。

抖音同理:签名往往来自 `a_bogus` / `_signature` 一类由客户端 JS 生成的字段,同样绑在真实 JS 运行时上。

## 3. 为什么用 Playwright 而不是纯 requests? 代价是什么?

**原因**: 签名和登录态都活在浏览器里。Playwright 给你的是「像人一样的 JS 运行时 + Cookie 罐」,不是更快的 HTTP 客户端。

**代价**:

| 维度 | Playwright | requests |
|---|---|---|
| 内存 | 一台 Chromium 常见 200–400MB+ | 几 MB |
| 启动 | 秒级 | 毫秒 |
| 速度 | 还要等页面/CDP | 直接打 API |
| 部署 | 要有显示或 headless 浏览器,CI/服务器都麻烦 | 一个容器即可 |
| 可观测性 | 可开窗口看验证码 | 只能看状态码 |

Lab 4 把 MediaCrawler **丢进子进程**,就是为了不让这份代价污染 Fishnet 主进程(热榜 / RSS 仍然是轻量 httpx + feedparser)。

## 4. 限速在哪一层? 并发调到 10 会怎样?(想清楚,别真试)

限速是**客户端自觉**,不是平台给你的配额 API:

- `MAX_CONCURRENCY_NUM = 1`(配置默认,本仓库强制保持 1)
- `CRAWLER_MAX_SLEEP_SEC = 2`(两次请求之间的睡眠)
- `CRAWLER_MAX_NOTES_COUNT = 15`(单次上限,我们再收到 10)

如果你把并发调到 10:

1. 同一登录态下短时间打出远超真人的 QPS;
2. 签名/设备指纹仍是同一份,平台很容易把「一个人开了十个标签」收成风控;
3. 结果是验证码、412/429、当天小黑屋、**封的是你的账号**而不只是 IP。

所以验收标准写死 **并发 = 1、间隔 ≥ 6 小时**。这不是性能调优,是站在许可与账号安全的一侧。
