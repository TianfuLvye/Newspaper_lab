# Lab 9.2:调度 + 采集 + 出报 + 推送。本机 CLI 仍可用;容器内跑 serve。
# 基础镜像走 ghcr(uv + Python 3.13),避开 Docker Hub 拉 python:slim 失败。
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        ffmpeg \
        tzdata \
        fontconfig \
        fonts-noto-cjk \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && uv run playwright install --with-deps chromium

COPY . .
RUN chmod +x docker/entrypoint.sh \
    && uv sync --frozen --no-dev

CMD ["./docker/entrypoint.sh"]
