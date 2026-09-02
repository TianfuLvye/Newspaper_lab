# ADR-011 · 全家桶进 Compose,本机 URL 用环境变量覆盖

- **状态**: 接受
- **Lab**: 9.2
- **日期**: 2026-09-02

## 上下文

Lab 6 把调度放在家里的 `uv run main.py serve`(ADR-005)。Lab 9.1 把邮件接到出报成功之后。手册 9.2 要求 `docker compose up -d` 一条命令拉起主程序 + DailyHotApi + RSSHub + Redis。

当时的 `docker-compose.yml` 只有 RSSHub 和 Redis;DailyHot 靠 README 里一条 `docker run`。主程序不在 Compose 里,换机器或重建容器没有单一入口。容器网络里 `127.0.0.1` 是 fishnet 自己,不是 RSSHub。

## 决策

1. **fishnet 作为 compose 服务跑 `main.py serve`。** `./data` 和 `./config` bind mount。重建容器不丢库、不丢报纸、不丢 `sources.yaml`。
2. **`settings.toml` 继续写本机回环地址。** 容器内用 `FISHNET_DAILYHOT_URL` / `FISHNET_RSSHUB_URL` / `FISHNET_WEWE_URL` 覆盖。这样本机 CLI 与容器调度可以共用同一份 toml。
3. **冷启动是「等一会儿 + 失败隔离」,不是健康检查卡死。** entrypoint 探依赖最多约 60s,超时也启动。RSSHub 晚到或挂了,下一轮采集再试,出报仍走 ADR-005。
4. **WeWe RSS 仍是 overlay。** 默认四件套不含公众号容器;合并 `docker-compose.wewe-rss.yml` 时才注入 `FISHNET_WEWE_URL`,并改写 `wechat.yaml` 里的 `127.0.0.1:4000`。
5. **密钥不进镜像。** `.dockerignore` 排除 `.env`;compose `env_file` 注入 SMTP / Cookie / LLM Key。

## 明确不做

- 不把 MediaCrawler 和扫码浏览器打进镜像。
- 不在本切片做 90 天归档、磁盘水位告警、GitHub Actions 心跳。
- 不把 `settings.toml` 改成 Docker DNS 主机名。
- 不在默认 compose 里起 WeWe。

## 后果

- **正面**: 新机器 `cp .env.example .env` 后一条 `docker compose up -d --build` 就能挂网;冷删除容器只要 `./data` 还在就能续跑。
- **负面**: 镜像含 Chromium 和 CJK 字体,体积大、首次构建慢。本机再开一份 `serve` 会和容器抢同一份 SQLite,需要先 `docker compose stop fishnet`。
- **成本**: 构建要能拉 PyPI(清华源)和 GitHub(`newspaper-layout`)。Playwright 需要 `shm_size`。

## 何时重新评估

1. 有固定在线的 NAS / 树莓派之后,把 compose 项目目录放到那台机器,开发笔记本只留 CLI。
2. 若要「采集在家、渲染在云」,再拆 PDF 服务,不要先把 Cookie 和 SQLite 送上公网。
3. DailyHot / RSSHub 官方镜像换 tag 或架构(ARM)出问题,再锁 digest。
