# Lab 9.2 · Docker Compose 全家桶

> **范围**: 一条命令拉起 fishnet + DailyHotApi + RSSHub + Redis。不做 Telegram、不做 90 天归档。  
> **决策**: 主程序进 compose;本机 URL 写在 `settings.toml`,容器内用 `FISHNET_*_URL` 覆盖。见 [ADR-011](./adr/011-compose-runtime.md)。

## 本 Lab 完成了什么

1. **`Dockerfile`**: Python 3.13 + uv + Playwright Chromium + 思源/Noto CJK 字体 + ffmpeg。入口 `docker/entrypoint.sh` → `main.py serve`。
2. **`docker-compose.yml`**: `fishnet` / `dailyhot` / `rsshub` / `redis`。`data/` 与 `config/` 绑到主机,重建容器报纸和库还在。
3. **服务名覆盖**: 容器里 `FISHNET_DAILYHOT_URL=http://dailyhot:6688`、`FISHNET_RSSHUB_URL=http://rsshub:1200`。本机 `uv run main.py` 仍打 `127.0.0.1`。
4. **冷启动**: entrypoint 先探 DailyHot / RSSHub 最多约 60s;探不到也启动。采集失败隔离,下一 tick 再试。`restart: unless-stopped` 崩了拉起来。
5. **WeWe 仍可选**: `-f docker-compose.wewe-rss.yml` 给 fishnet 注入 `FISHNET_WEWE_URL`,并把 `wechat.yaml` 里的 `127.0.0.1:4000` 改写成 `wewe-rss`。
6. **测试**: `uv run python -m tests.test_lab9`(含 compose 结构 / URL 覆盖,不强制本机 `docker compose up`)。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| `docker compose up -d` 一条命令 | `docker-compose.yml` 四服务 |
| 冷启动:删容器重建能继续出报 | bind mount `./data`;entrypoint 等待;失败隔离 |
| README 写清部署 | 仓库 `README.md`「部署(Lab 9.2)」 |
| 密钥不进镜像 | `.dockerignore` 排除 `.env`;`env_file` 注入 |
| 本机 CLI 不坏 | `settings.toml` 仍是 127.0.0.1;端口 6688/1200 照旧映射 |

连续 7 天早晚两报是运行时验收,代码切片不替你收 14 封邮件。90 天归档 / 磁盘水位告警见 9.3。

## 模块与函数设计笔记

### `docker-compose.yml` · `fishnet`

- **目的**: 把 Lab 6 的 `serve` 从笔记本进程变成可重启的容器。
- **为什么 `settings.toml` 不改成 `http://rsshub:1200`**: 那会让本机 `uv run main.py collect` 失效。覆盖走环境变量,compose `environment` 优先于 `env_file`。
- **为什么 `shm_size: 1gb`**: Playwright Chromium 默认 `/dev/shm` 太小会静默崩,PDF 出不来。
- **刻意不做**: 不把 MediaCrawler / 扫码浏览器放进镜像(Lab 4 仍在主机)。不把 WeWe 绑进默认四件套。

### `docker/entrypoint.sh`

- **目的**: 冷启动时依赖还没 listen,避免第一分钟调度全红。
- **为什么超时也继续**: ADR-005 残缺出报。RSSHub 挂了报纸也要出。

### `core/settings.py` · `url_from_env` / `rewrite_wewe_loopback`

- **目的**: 容器内解析到 Docker DNS;已有 `wechat.yaml` 不用手改。
- **为什么只改写 `:4000`**: 避免把别的 127.0.0.1 服务误伤。`{wewe}` 占位给新配置用。

### `Dockerfile`

- **目的**: 可重复的运行时,含排版所需 Chromium 和中文字体。
- **踩坑**: `newspaper-layout` 是 git 依赖,构建机要能访问 GitHub。Debian slim 没有 CJK 字体会出方块字 PDF。`uv.lock` 必须进镜像。基础镜像用 `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`,国内 Docker Hub 拉 `python:slim` 经常 EOF。

## 本地怎么验收

```bash
cd fishnet-reference
cp .env.example .env   # 已有 .env 可跳过;SMTP 仍按 9.1 填

# 若以前 docker run --name dailyhot 占着 6688:
# docker rm -f dailyhot

docker compose up -d --build
docker compose ps
# fishnet / dailyhot / rsshub / redis 应为 running(或 restarting 后变 running)

# 结构单测(不要求 Docker 守护进程)
uv run python -m tests.test_lab9

# 看调度是否起来
docker compose logs -f fishnet

# 冷启动:删容器但保留 ./data
docker compose down
docker compose up -d
# data/fishnet.db 与 data/editions/ 应还在;serve 继续跑

# 停全家桶,本机调试时不要叠跑两份 serve
docker compose stop fishnet
uv run main.py serve
```

公众号:

```bash
docker compose -f docker-compose.yml -f docker-compose.wewe-rss.yml up -d
```

## 留给下一切片的接口

- 9.3:`items_archive`、磁盘水位告警。体检页已有库体积,告警通道可复用 SMTP。
- Telegram / 飞书仍走 `notify/channels.py` 的 skipped 名单。
- GitHub Actions 心跳仍未做(ADR-005)。
