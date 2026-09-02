# Lab 9 · 推送:通道选择 + SMTP 邮件

> **范围**: 9.1 选通道,并把已有 `digest.pdf` 发到邮箱。不做客户端、不做 Telegram。Docker 全家桶见 [lab-09-compose.md](./lab-09-compose.md)。  
> **决策**: 邮件为主通道;正文是摘要,PDF 是附件。见 [ADR-010](./adr/010-notify-email.md)。

## 本 Lab 完成了什么

1. **通道选择**: `notify/channels.py` 认 `email`(发)和 `telegram` / `feishu` / `client`(跳过)。未知名字直接报错。
2. **邮件摘要**: `notify/compose.py` 读 `articles.json`,写出可在手机上扫读的 text/html,不把 A3 `digest.html` 内联进去。
3. **SMTP 发送**: `notify/email.py` 用标准库。PDF 默认当附件;超 `max_attach_mb` 就只发摘要。
4. **CLI**: `uv run main.py push --edition 2026-09-01-pm`;`--dry-run` 不连服务器;`--force` 忽略已发送记录。
5. **调度**: `serve` 在出报 `status != failed` 之后立刻 push。SMTP 没配则跳过。`notify.json` 防止同一期连发两次。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 邮件发 PDF 附件(主通道) | `channels = ["email"]` + `digest.pdf` 附件 |
| 打开邮件就能读 | HTML 正文是标题目录,不是 A3 印刷页 |
| 密钥不进仓库 | `FISHNET_SMTP_*` 在 `.env`;`.gitignore` 已有 |
| 子命令独立可跑 | 没配 SMTP 时 `push` 返回 0 并打印跳过 |
| 出报后能送到面前 | `_job_edition` 成功后调 `_job_push` |

连续 7 天早晚两报、90 天归档、磁盘水位仍属 9.3 / 运行验收,本切片不验收。Docker 全家桶见 [lab-09-compose.md](./lab-09-compose.md)。

## 模块与函数设计笔记

### `notify/channels.py` · `select_channels`

- **目的**: 把配置里的名字分成「现在发」和「本切片故意不做」。
- **为什么未知通道要抛错**: `emial` 这种 typo 若当成「没开推送」,报纸会静默消失。
- **刻意不做**: 不在这里发 HTTP、不读 token。

### `notify/compose.py` · `compose_digest`

- **目的**: 一期目录 → 主题 / 纯文本 / 简单 HTML / 附件列表。
- **为什么不内联 `digest.html`**: 约数 MB,打印 CSS 和 `--paper-width` 邮件客户端处理不了。手册 Lab 8 说的「HTML 内联」指的是可读摘要,不是 A3 拼版页。
- **输入**: 优先 `articles.json`(Lab 8 印刷管线),没有则扫 `digest.md` 的 `##` 标题。不用 `edition.json`,避免绑客户端契约。
- **体检不出镜**: 系统页给纸上看,邮件目录留给要读的稿。

### `notify/email.py` · `build_message` / `send_message`

- **目的**: `EmailMessage` + `smtplib`。465 SSL,587 STARTTLS,可由 `FISHNET_SMTP_STARTTLS` 覆盖。
- **为什么标准库**: 个人日报不值得再加一个 SDK;失败要抛给调用方,不要吞掉。
- **踩坑**: QQ / 163 必须用授权码。`send_message` 的拒绝名单当失败。

### `notify/push.py` · `push_edition_dir`

- **目的**: 通道编排、幂等、给 CLI / 调度同一条路。
- **`notify.json`**: 成功才写。调度补跑或人手重复 `push` 不会再骚扰收件箱。
- **没配 SMTP**: `skipped`,不是 `failed`。家里还没填授权码时 `serve` 仍能出报。

### `scheduler/run.py` · `_job_push`

- **目的**: 出报函数返回后再发,保证 PDF 已经落盘。
- **为什么不另挂 07:30 cron**: 出报有 20 分钟 deadline,固定钟点可能抢跑。
- **失败隔离**: push 异常只记日志,不改变 edition 的 status。

## 本地怎么验收

```bash
# 不连邮箱
uv run python -m tests.test_lab9
uv run main.py push --dry-run --edition 2026-09-01-pm

# 真发一封:复制 .env.example 里 FISHNET_SMTP_* ,QQ/163 填授权码
uv run main.py push --edition 2026-09-01-pm
# 再跑一次应显示 skipped(已有 notify.json)
uv run main.py push --edition 2026-09-01-pm
uv run main.py push --edition 2026-09-01-pm --force
```

常驻路径: `uv run main.py serve` 在 07:00 / 19:00 出报成功后自动 push。

## 留给下一 Lab 的接口

- Telegram / 飞书:在 `IMPLEMENTED` 里登记,复用 `DigestMail` 做卡片,不要重解析期次。
- 9.3:90 天归档、磁盘水位告警仍未做。
- 客户端继续走 `main.py client`,与邮件互不影响。
