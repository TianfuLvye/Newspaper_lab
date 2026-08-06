"""Fishnet 统一 CLI 入口(Lab 0)。

用法示例:
  uv run main.py --help
  uv run main.py collect --only dummy
  uv run main.py stats
  uv run main.py collect --only dummy && uv run main.py stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from collectors.dummy import DummyCollector
from core.base import run_collector
from core.store import Store

# Lab 0:先用手写字典注册。Lab 后续可用 registry.py 自动扫描 collectors/。
COLLECTORS: dict[str, type] = {
    "dummy": DummyCollector,
}

DEFAULT_DB = Path("data/fishnet.db")


def cmd_collect(args: argparse.Namespace) -> int:
    """跑一个或多个采集器,把结果幂等写入 SQLite。"""
    store = Store(args.db)
    try:
        names = [args.only] if args.only else sorted(COLLECTORS)
        unknown = [n for n in names if n not in COLLECTORS]
        if unknown:
            print(f"未知 collector: {', '.join(unknown)}", file=sys.stderr)
            print(f"可选: {', '.join(sorted(COLLECTORS))}", file=sys.stderr)
            return 1

        for name in names:
            new, dup = run_collector(COLLECTORS[name](), store)
            print(f"[{name}] new={new} dup={dup}")
        return 0
    finally:
        store.close()


def cmd_stats(args: argparse.Namespace) -> int:
    """打印库规模,以及最近一次成功采集的 new / dup。"""
    store = Store(args.db)
    try:
        n_items = store.stats()["items"]
        row = store._conn.execute(
            "SELECT collector, new_count, item_count, status "
            "FROM collector_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print(f"{n_items} items, (尚无采集记录)")
            return 0

        new = row["new_count"] or 0
        total = row["item_count"] or 0
        dup = max(total - new, 0)
        print(f"{n_items} items, {new} new, {dup} dup")
        if row["status"] != "ok":
            print(
                f"(最近一次 [{row['collector']}] 状态={row['status']}, "
                "上面的 new/dup 可能无意义)",
                file=sys.stderr,
            )
        return 0
    finally:
        store.close()


def cmd_render(args: argparse.Namespace) -> int:
    print("render: 尚未实现 —— 见 Lab 8(Markdown → PDF)")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    print("push: 尚未实现 —— 见 Lab 9(邮件 / Telegram 推送)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fishnet 个人情报报纸的命令行入口。\n"
            "Lab 0 先跑通:采集(collect) → 入库 → 看统计(stats)。\n"
            "render / push 只是占位,后面的 Lab 再实现。"
        ),
        epilog=(
            "常见流程(验证幂等):\n"
            "  1) uv run main.py collect --only dummy\n"
            "  2) uv run main.py stats          # 期望: 1 items, 1 new, 0 dup\n"
            "  3) uv run main.py collect --only dummy\n"
            "  4) uv run main.py stats          # 期望: 1 items, 0 new, 1 dup\n"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=(
            f"SQLite 数据库路径(默认: {DEFAULT_DB})。"
            "首次 collect / stats 时若不存在会自动创建。"
        ),
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="要执行的子命令,见下方各命令说明",
    )

    # ---- collect ----
    p_collect = sub.add_parser(
        "collect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="运行采集器,把抓到的 Item 幂等写入数据库",
        description=(
            "运行一张或多张「渔网」(collector)。\n"
            "每张网只负责产出 Item;入库、记 collector_runs、失败隔离\n"
            "都由 core.base.run_collector 统一处理。\n"
            "\n"
            "同一条内容重复采集不会膨胀库(content_hash + INSERT OR IGNORE)。"
        ),
        epilog=(
            "示例:\n"
            "  uv run main.py collect --only dummy   # 只跑假采集器\n"
            "  uv run main.py collect                # 跑全部已注册采集器"
        ),
    )
    p_collect.add_argument(
        "--only",
        metavar="NAME",
        help=(
            "只跑名为 NAME 的采集器。"
            f"当前已注册: {', '.join(sorted(COLLECTORS))}。"
            "省略本参数则按名字排序跑完全部。"
        ),
    )
    p_collect.set_defaults(func=cmd_collect)

    # ---- stats ----
    p_stats = sub.add_parser(
        "stats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="查看库里有多少条,以及最近一次采集的新增/重复数",
        description=(
            "打印一行摘要,格式接近 Lab 0 验收文案:\n"
            "  <总条数> items, <新增> new, <重复> dup\n"
            "\n"
            "总条数来自 items 表;"
            "new/dup 来自 collector_runs 里最近一次记录\n"
            "(dup ≈ 该次采到的条数 - new_count)。"
        ),
        epilog="示例: uv run main.py stats",
    )
    p_stats.set_defaults(func=cmd_stats)

    # ---- render / push 占位 ----
    p_render = sub.add_parser(
        "render",
        help="渲染报纸(Lab 8 占位:目前只打印提示,不做事)",
        description=(
            "把已打分、已选用的内容渲染成 Markdown / PDF。\n"
            "Lab 0 验收只要求子命令出现在 --help 里,这里先留空实现。"
        ),
    )
    p_render.set_defaults(func=cmd_render)

    p_push = sub.add_parser(
        "push",
        help="推送报纸(Lab 9 占位:目前只打印提示,不做事)",
        description=(
            "把渲染好的报纸发到邮箱 / Telegram 等渠道。\n"
            "Lab 0 验收只要求子命令出现在 --help 里,这里先留空实现。"
        ),
    )
    p_push.set_defaults(func=cmd_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
