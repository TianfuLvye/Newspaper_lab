#!/bin/sh
# Lab 9.2:冷启动时 DailyHot / RSSHub 可能比 fishnet 晚就绪。
# 最多等约 60s,超时也启动——采集失败隔离,下一 tick 会再试。
set -eu

wait_url() {
    url="$1"
    name="$2"
    i=0
    while [ "$i" -lt 30 ]; do
        if python -c "import urllib.request; urllib.request.urlopen('$url', timeout=2)" 2>/dev/null; then
            echo "ready: $name ($url)"
            return 0
        fi
        i=$((i + 1))
        sleep 2
    done
    echo "timeout waiting for $name at $url; starting anyway"
    return 0
}

wait_url "${FISHNET_DAILYHOT_URL:-http://dailyhot:6688}" dailyhot
wait_url "${FISHNET_RSSHUB_URL:-http://rsshub:1200}" rsshub

exec python main.py serve
