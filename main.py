"""Fishnet 统一 CLI 入口(Lab 0 / Lab 1)。

用法示例:
  uv run main.py --help
  uv run main.py collect --only hotlist_weibo
  uv run main.py collect                 # 跑 sources.yaml 里全部热榜(+可选 dummy)
  uv run main.py stats
  uv run main.py render --section hotlist
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.base import run_collector
from core.registry import all_collectors, get_collector, list_collector_names
from core.settings import load_hotlist_sources, load_settings
from core.store import Store
from render.hotlist import write_hotlist_section

DEFAULT_DB = Path("data/fishnet.db")


def _resolve_collectors(only: str | None, include_dummy: bool):
    if only:
        c = get_collector(only)
        if c is None:
            return None, [only]
        return [c], []
    return all_collectors(include_dummy=include_dummy), []


def cmd_collect(args: argparse.Namespace) -> int:
    """跑一个或多个采集器,把结果幂等写入 SQLite。"""
    store = Store(args.db)
    try:
        collectors, unknown = _resolve_collectors(args.only, args.include_dummy)
        if unknown:
            print(f"未知 collector: {', '.join(unknown)}", file=sys.stderr)
            print(
                f"可选: {', '.join(list_collector_names(include_dummy=True))}",
                file=sys.stderr,
            )
            return 1
        assert collectors is not None
        if not collectors:
            print("没有可运行的 collector(检查 config/sources.yaml)", file=sys.stderr)
            return 1

        failed = 0
        for c in collectors:
            new, dup = run_collector(c, store)
            # run_collector 失败时返回 (0,0) 且会写 failed;用 health 粗判
            print(f"[{c.name}] new={new} dup={dup}")
            row = store._conn.execute(
                "SELECT status FROM collector_runs WHERE collector=? "
                "ORDER BY id DESC LIMIT 1",
                (c.name,),
            ).fetchone()
            if row and row["status"] != "ok":
                failed += 1
        return 1 if failed and args.strict else 0
    finally:
        store.close()


def cmd_stats(args: argparse.Namespace) -> int:
    """打印库规模、分源计数,以及最近一次采集的 new / dup。"""
    store = Store(args.db)
    try:
        st = store.stats()
        n_items = st["items"]
        row = store._conn.execute(
            "SELECT collector, new_count, item_count, status "
            "FROM collector_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print(f"{n_items} items, (尚无采集记录)")
        else:
            new = row["new_count"] or 0
            total = row["item_count"] or 0
            dup = max(total - new, 0)
            print(f"{n_items} items, {new} new, {dup} dup")
            if row["status"] != "ok":
                print(
                    f"(最近一次 [{row['collector']}] 状态={row['status']})",
                    file=sys.stderr,
                )
        if st["by_source"]:
            parts = [f"{k}={v}" for k, v in sorted(st["by_source"].items())]
            print("by_source: " + ", ".join(parts))
        return 0
    finally:
        store.close()


def cmd_render(args: argparse.Namespace) -> int:
    """Lab 1:渲染热榜新上榜片段;其它 section 仍占位。"""
    if args.section not in (None, "hotlist", "all"):
        print(f"未知 section: {args.section}(Lab 1 仅支持 hotlist)", file=sys.stderr)
        return 1

    store = Store(args.db)
    try:
        boards = [r["board"] for r in load_hotlist_sources()]
        path = write_hotlist_section(
            store,
            boards,
            window_hours=args.window_hours,
            limit=args.limit,
        )
        print(f"wrote {path}")
        return 0
    finally:
        store.close()


def cmd_push(args: argparse.Namespace) -> int:
    print("push: 尚未实现 —— 见 Lab 9(邮件 / Telegram 推送)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    names = ", ".join(list_collector_names(include_dummy=True))

    parser = argparse.ArgumentParser(
        prog="main.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fishnet 个人情报报纸的命令行入口。\n"
            "Lab 0: dummy 幂等验收。\n"
            "Lab 1: DailyHotApi 热榜采集 → newly_entered → hotlist.md。"
        ),
        epilog=(
            "Lab 1 快速验收:\n"
            "  1) docker run -d --name dailyhot -p 6688:6688 "
            "-e ALLOWED_DOMAIN='*' -e ALLOWED_HOST=0.0.0.0 "
            "imsyy/dailyhot-api:latest\n"
            "  2) uv run main.py collect\n"
            "  3) uv run main.py stats\n"
            "  4) uv run main.py render --section hotlist\n"
            "  5) 长时间稳定性: uv run python -m tests.test_lab1_endurance "
            "--hours 6\n"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite 路径(默认: {DEFAULT_DB})。",
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="要执行的子命令,见下方各命令说明",
    )

    p_collect = sub.add_parser(
        "collect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="运行采集器,把抓到的 Item 幂等写入数据库",
        description=(
            "运行一张或多张「渔网」(collector)。\n"
            "默认读取 config/sources.yaml 里的全部热榜网。\n"
            "入库 / 快照 / collector_runs 由 run_collector 统一处理。"
        ),
        epilog=(
            "示例:\n"
            "  uv run main.py collect --only hotlist_weibo\n"
            "  uv run main.py collect --include-dummy\n"
            "  uv run main.py collect"
        ),
    )
    p_collect.add_argument(
        "--only",
        metavar="NAME",
        help=f"只跑名为 NAME 的采集器。可选: {names}",
    )
    p_collect.add_argument(
        "--include-dummy",
        action="store_true",
        help="在跑全部热榜时,额外包含 Lab 0 的 dummy 采集器",
    )
    p_collect.add_argument(
        "--strict",
        action="store_true",
        help="任一 collector 失败则进程退出码为 1(默认:失败隔离,仍返回 0)",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_stats = sub.add_parser(
        "stats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="查看库规模、分源计数、最近一次 new/dup",
        description=(
            "打印 items 总数、最近一次采集 new/dup,以及 GROUP BY source 计数。"
        ),
    )
    p_stats.set_defaults(func=cmd_stats)

    p_render = sub.add_parser(
        "render",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="渲染报纸片段(Lab 1: hotlist 新上榜 Top 20)",
        description=(
            "Lab 1 实现 --section hotlist:\n"
            "基于 newly_entered() 写出 render/sections/hotlist.md。"
        ),
    )
    p_render.add_argument(
        "--section",
        default="hotlist",
        help="要渲染的版面(默认 hotlist;Lab 1 仅此一个)",
    )
    p_render.add_argument(
        "--window-hours",
        type=int,
        default=6,
        help="「新上榜」时间窗小时数(默认 6)",
    )
    p_render.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Top N(默认 20)",
    )
    p_render.set_defaults(func=cmd_render)

    p_push = sub.add_parser(
        "push",
        help="推送报纸(Lab 9 占位)",
        description="Lab 9 再实现邮件 / Telegram 推送。",
    )
    p_push.set_defaults(func=cmd_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
