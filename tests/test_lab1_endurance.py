"""Lab 1 长时间稳定性验收。

验收标准原文:至少 5 个平台热榜稳定入库,连跑 6 小时无崩溃。

用法:
  # 你自己跑完整 6 小时
  uv run python -m tests.test_lab1_endurance --hours 6

  # 开发冒烟(~3 分钟,默认间隔 60s,约 3 轮)
  uv run python -m tests.test_lab1_endurance --minutes 3 --interval 60

退出码:
  0 = 全程无崩溃,且至少一轮「≥5 个平台成功入库」
  1 = 参数/配置错误,或未达成功门槛
  2 = 出现未捕获异常(真崩溃)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 单板失败会被安全壳吞掉;连跑时不要把整段 traceback 刷屏
logging.getLogger("fishnet").setLevel(logging.ERROR)

from core.base import run_collector
from core.registry import all_collectors
from core.settings import load_settings
from core.store import Store
from render.hotlist import write_hotlist_section


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Lab 1 热榜连跑稳定性测试(默认 6 小时;开发可用 --minutes 3)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hours", type=float, help="连跑小时数(验收用 6)")
    g.add_argument("--minutes", type=float, help="连跑分钟数(冒烟用 3)")
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="两轮采集间隔秒数(默认: minutes 模式 60, hours 模式 1800=30min)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=Path("data/fishnet_endurance.db"),
        help="专用测试库,避免污染日常 fishnet.db",
    )
    p.add_argument(
        "--min-ok-boards",
        type=int,
        default=5,
        help="单轮至少多少个平台 status=ok 才算达标(默认 5)",
    )
    p.add_argument(
        "--max-consecutive-fail-rounds",
        type=int,
        default=3,
        help="连续多少轮「零平台成功」则判定失败退出(默认 3)",
    )
    args = p.parse_args(argv)
    if args.hours is None and args.minutes is None:
        args.hours = 6.0
    if args.interval is None:
        args.interval = 60.0 if args.minutes is not None else 1800.0
    return args


def _duration_seconds(args: argparse.Namespace) -> float:
    if args.minutes is not None:
        return args.minutes * 60.0
    return float(args.hours) * 3600.0


def run_round(store: Store, min_ok: int) -> tuple[int, int, dict[str, str]]:
    """返回 (ok_count, fail_count, {name: status})。"""
    statuses: dict[str, str] = {}
    ok = fail = 0
    for c in all_collectors(include_dummy=False):
        run_collector(c, store)
        row = store._conn.execute(
            "SELECT status, error FROM collector_runs WHERE collector=? "
            "ORDER BY id DESC LIMIT 1",
            (c.name,),
        ).fetchone()
        status = row["status"] if row else "missing"
        statuses[c.name] = status
        if status == "ok":
            ok += 1
        else:
            fail += 1
            err = (row["error"] if row else "") or ""
            print(f"    ! {c.name} -> {status} {err[:120]}")
    print(f"    ok={ok} fail={fail} (need>={min_ok})")
    return ok, fail, statuses


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    duration = _duration_seconds(args)
    settings = load_settings()
    boards = [c.board for c in all_collectors(include_dummy=False) if c.board]

    print("=" * 60)
    print("Lab 1 endurance")
    print(f"  dailyhot : {settings.dailyhot_url}")
    print(f"  boards   : {boards}")
    print(f"  duration : {duration:.0f}s  interval={args.interval:.0f}s")
    print(f"  db       : {args.db}")
    print("=" * 60)

    # 连通性预检:不绑定单一榜(weibo 上游常 500),至少 1 个榜有数据即可开跑
    import httpx
    preflight_ok = False
    for b in boards or ["zhihu"]:
        try:
            r = httpx.get(f"{settings.dailyhot_url}/{b}", timeout=20)
            n = len(r.json().get("data") or []) if r.status_code == 200 else 0
            print(f"preflight {b}: http={r.status_code} n={n}")
            if n > 0:
                preflight_ok = True
        except Exception as e:
            print(f"preflight {b}: ERR {e!r}")
    if not preflight_ok:
        print("preflight FAIL: no board returned data", file=sys.stderr)
        print(
            "hint: docker run -d --name dailyhot -p 6688:6688 "
            "-e ALLOWED_DOMAIN='*' -e ALLOWED_HOST=0.0.0.0 imsyy/dailyhot-api:latest",
            file=sys.stderr,
        )
        return 1

    store = Store(args.db)
    t0 = time.monotonic()
    round_i = 0
    best_ok = 0
    consec_fail = 0
    ever_met_threshold = False

    try:
        while True:
            round_i += 1
            elapsed = time.monotonic() - t0
            if elapsed > duration and round_i > 1:
                break
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n[{ts}] round {round_i}  elapsed={elapsed:.0f}s/{duration:.0f}s")
            try:
                ok, fail, _ = run_round(store, args.min_ok_boards)
            except Exception:
                print("CRASH in round:", file=sys.stderr)
                traceback.print_exc()
                store.close()
                return 2

            best_ok = max(best_ok, ok)
            if ok >= args.min_ok_boards:
                ever_met_threshold = True
                consec_fail = 0
            elif ok == 0:
                consec_fail += 1
            else:
                consec_fail = 0

            if consec_fail >= args.max_consecutive_fail_rounds:
                print(
                    f"FAIL: {consec_fail} consecutive rounds with zero ok boards",
                    file=sys.stderr,
                )
                store.close()
                return 1

            # 每轮顺便刷新 md,确认渲染路径不崩
            try:
                path = write_hotlist_section(
                    store, [b for b in boards if b],
                    out_path=Path("render/sections/hotlist.md"),
                )
                print(f"    render -> {path}")
            except Exception:
                print("CRASH in render:", file=sys.stderr)
                traceback.print_exc()
                store.close()
                return 2

            if time.monotonic() - t0 >= duration:
                break
            sleep_for = max(0.0, args.interval - 0.01)
            print(f"    sleep {sleep_for:.0f}s ...")
            time.sleep(sleep_for)

        st = store.stats()
        print("\n" + "=" * 60)
        print(f"finished rounds={round_i} best_ok_boards={best_ok}")
        print(f"items={st['items']} by_source={st['by_source']}")
        print("=" * 60)
        store.close()

        if not ever_met_threshold:
            print(
                f"FAIL: never reached {args.min_ok_boards} successful boards in one round",
                file=sys.stderr,
            )
            return 1
        print("PASS: no crash, threshold met at least once")
        return 0
    except Exception:
        traceback.print_exc()
        try:
            store.close()
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
