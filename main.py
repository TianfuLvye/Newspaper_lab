"""Fishnet 统一 CLI 入口(Lab 0–8)。

用法示例:
  uv run main.py --help
  uv run main.py collect --only hotlist_weibo
  uv run main.py collect --only-rss
  uv run main.py collect --only-targeted # Lab 4,不进默认 collect
  uv run main.py collect                 # 热榜 + RSS 订阅
  uv run main.py enrich --limit 20       # Lab 5 正文抽取 + B 站白名单发现
  uv run main.py transcript BV19d4y1D7n3 # 口播：下载音频 → 火山 STT → 改稿
  uv run main.py render --edition am     # Lab 6/7/8 出一期早报(含 PDF)
  uv run main.py pdf                     # 对已有 digest 只排版,不重跑打分
  uv run main.py golden                  # Lab 7 拟合收藏夹画像
  uv run main.py ab --kind am            # Lab 7 热度 vs 打分对照
  uv run main.py feedback --edition DATE-am --n 1 --label 1
  uv run main.py health                  # Lab 6 系统体检
  uv run main.py serve                   # Lab 6 常驻调度
  uv run main.py console                 # 本机网页控制台，改订阅源
  uv run main.py stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.base import run_collector
from core.registry import all_collectors, get_collector, list_collector_names
from core.settings import load_env_file, load_hotlist_sources, load_settings
from core.store import Store
from render.hotlist import write_hotlist_section
from render.subscriptions import write_subscriptions_section

DEFAULT_DB = Path("data/fishnet.db")


def _resolve_collectors(
    only: str | None,
    include_dummy: bool,
    *,
    only_rss: bool = False,
    only_hotlist: bool = False,
    only_targeted: bool = False,
):
    flags = [only_rss, only_hotlist, only_targeted]
    if sum(bool(x) for x in flags) > 1:
        print("不能同时指定 --only-rss / --only-hotlist / --only-targeted", file=sys.stderr)
        return None, ["--only-*"]
    if only:
        c = get_collector(only)
        if c is None:
            return None, [only]
        return [c], []
    if only_rss:
        return all_collectors(
            include_dummy=include_dummy, include_hotlist=False, include_rss=True
        ), []
    if only_hotlist:
        return all_collectors(
            include_dummy=include_dummy, include_hotlist=True, include_rss=False
        ), []
    if only_targeted:
        return all_collectors(
            include_dummy=include_dummy,
            include_hotlist=False,
            include_rss=False,
            include_targeted=True,
        ), []
    return all_collectors(include_dummy=include_dummy), []


def cmd_collect(args: argparse.Namespace) -> int:
    """跑一个或多个采集器,把结果幂等写入 SQLite。"""
    store = Store(args.db)
    try:
        collectors, unknown = _resolve_collectors(
            args.only,
            args.include_dummy,
            only_rss=args.only_rss,
            only_hotlist=args.only_hotlist,
            only_targeted=args.only_targeted,
        )
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
        print(
            f"with_content={st['with_content']} "
            f"missing={n_items - st['with_content']}"
        )
        if st["by_source"]:
            parts = [f"{k}={v}" for k, v in sorted(st["by_source"].items())]
            print("by_source: " + ", ".join(parts))
        return 0
    finally:
        store.close()


def cmd_render(args: argparse.Namespace) -> int:
    """渲染报纸片段,或出一期早报/晚报。"""
    if args.edition:
        from pipeline.edition import produce_edition

        if args.edition not in ("am", "pm"):
            print("edition 必须是 am 或 pm", file=sys.stderr)
            return 1
        load_env_file()
        store = Store(args.db)
        try:
            result = produce_edition(args.edition, store, out_dir=args.out_dir)
            print(f"edition {result.edition_id} status={result.status}")
            print(f"wrote {result.digest_path}")
            html = result.digest_path.with_name("digest.html")
            pdf = result.digest_path.with_name("digest.pdf")
            if html.exists():
                print(f"wrote {html}")
            if pdf.exists():
                print(f"wrote {pdf}")
            elif not pdf.exists():
                print("pdf missing (see log; HTML 仍可打印)", file=sys.stderr)
            if result.failures:
                for name, err in result.failures:
                    print(f"  fail [{name}] {err}", file=sys.stderr)
            return 0 if result.status != "failed" else 1
        finally:
            store.close()

    section = args.section or "hotlist"
    if section not in ("hotlist", "subscriptions", "all"):
        print(
            f"未知 section: {section}(支持 hotlist / subscriptions / all)",
            file=sys.stderr,
        )
        return 1

    store = Store(args.db)
    try:
        wrote: list[Path] = []
        if section in ("hotlist", "all"):
            boards = [r["board"] for r in load_hotlist_sources()]
            path = write_hotlist_section(
                store,
                boards,
                window_hours=args.window_hours,
                limit=args.limit,
            )
            wrote.append(path)
        if section in ("subscriptions", "all"):
            path = write_subscriptions_section(
                store,
                window_hours=args.sub_window_hours,
                limit=args.sub_limit,
            )
            wrote.append(path)
        for p in wrote:
            print(f"wrote {p}")
        return 0
    finally:
        store.close()


def cmd_enrich(args: argparse.Namespace) -> int:
    """Lab 5 抽网页正文；并列出 B 站白名单视频（不转写）。"""
    from enrich.bilibili import enrich_bilibili
    from enrich.extract import enrich_store

    store = Store(args.db)
    try:
        bili = enrich_bilibili(store)
        print(
            f"bili ups={bili['ups']} collections={bili['collections']} "
            f"videos_new={bili['videos_new']} videos_dup={bili['videos_dup']} "
            f"catalog={bili['catalog']} error={bili['error']}"
        )
        stats = enrich_store(store, limit=args.limit)
        print(
            f"enrich ok={stats['ok']} degraded={stats['degraded']} "
            f"blocked={stats['blocked']} error={stats['error']} "
            f"cached={stats['cached']} restored={stats.get('restored', 0)}"
        )
        return 0
    finally:
        store.close()


def cmd_transcript(args: argparse.Namespace) -> int:
    """手动转写一条 B 站口播：下载音频 → 火山 STT → Flash 改稿。不改 enrich。"""
    from core.settings import load_env_file
    from enrich.transcript import run_transcript

    load_env_file()
    store = Store(args.db)
    try:
        result = run_transcript(
            args.video,
            store=store,
            from_text=Path(args.from_text) if args.from_text else None,
        )
        print(
            f"transcript {result.bvid} mode={result.mode} "
            f"wrote={result.newspaper_path} content={int(result.content_written)}"
        )
        if result.layout_path:
            print(f"layout {result.layout_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 — 命令行要把下载/STT/改稿错误打出来
        print(f"transcript failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


def cmd_health(args: argparse.Namespace) -> int:
    """打印系统体检 Markdown(与报纸最后一页同一份报告)。"""
    from pipeline.health import diagnose
    from render.health import render_health_md

    store = Store(args.db)
    try:
        report = diagnose(store)
        print(render_health_md(report), end="")
        return 0 if report.ok else 1
    finally:
        store.close()


def cmd_serve(args: argparse.Namespace) -> int:
    """启动 APScheduler 常驻进程。"""
    from scheduler.run import serve

    return serve(db_path=args.db, include_targeted=not args.no_targeted)


def cmd_console(args: argparse.Namespace) -> int:
    """本机网页控制台：增删改 RSS 订阅源。"""
    import uvicorn

    from console.app import create_app

    load_env_file()
    app = create_app(db_path=args.db)
    print(f"订阅台 http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_pdf(args: argparse.Namespace) -> int:
    """对已有一期 Markdown 做 A3 矩阵排版。不打 used_in,不跑采集。"""
    from core.settings import load_settings
    from render.newspaper import render_newspaper

    settings = load_settings()
    dest: Path | None = None
    if args.dir:
        dest = Path(args.dir)
    elif args.edition:
        dest = settings.editions_dir / args.edition
    else:
        root = settings.editions_dir
        if root.exists():
            dirs = sorted(
                (
                    p
                    for p in root.iterdir()
                    if p.is_dir() and not p.name.startswith("_")
                ),
                key=lambda p: p.name,
                reverse=True,
            )
            dest = dirs[0] if dirs else None
    if dest is None or not dest.exists():
        print("找不到期次目录。先 render --edition am,或指定 --edition / --dir。", file=sys.stderr)
        return 1
    kind = None
    if dest.name.endswith("-pm"):
        kind = "pm"
    elif dest.name.endswith("-am"):
        kind = "am"
    result = render_newspaper(dest, kind=kind, pdf=not args.html_only)
    print(f"edition {result.edition_id} kind={result.kind} pages={result.layout.n_pages}")
    print(f"wrote {result.html_path}")
    print(f"wrote {result.layout_path}")
    if result.pdf_path:
        print(f"wrote {result.pdf_path} ({result.seconds:.2f}s)")
    if result.error:
        print(f"pdf error: {result.error}", file=sys.stderr)
        return 1
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    print("push: 尚未实现 —— 见 Lab 9(邮件 / Telegram 推送)")
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    """拟合或刷新黄金集画像。不写出报,不碰 used_in。"""
    from pipeline.golden import (
        append_jsonl,
        fit_taste,
        import_zhihu_collections,
        load_golden,
        save_taste,
    )

    imported = []
    if args.refresh:
        imported = import_zhihu_collections()
        n = append_jsonl(imported)
        print(f"zhihu imported={len(imported)} appended={n}")
    docs = load_golden()
    print(f"golden docs={len(docs)}")
    if len(docs) < args.min_docs:
        print(
            f"警告:不足 {args.min_docs} 篇(验收线)。"
            "在 config/golden.yaml 填 zhihu_collections 后加 --refresh。",
            file=sys.stderr,
        )
    fitted = fit_taste(docs)
    path = save_taste(fitted)
    print(
        f"profile k={fitted.profile.k} dim={fitted.embedder.dim} "
        f"mu_len={fitted.profile.mu_len:.2f} -> {path}"
    )
    for name, n in sorted(fitted.clusters.items()):
        print(f"  cluster {name}: {n}")
    return 0 if len(docs) >= args.min_docs else 1


def cmd_feedback(args: argparse.Namespace) -> int:
    """记录有用/无用。编号来自该期 ranking.json。"""
    import json

    store = Store(args.db)
    try:
        content_hash = args.hash
        if args.n is not None:
            settings = load_settings()
            manifest = settings.editions_dir / args.edition / "ranking.json"
            if args.out_dir:
                manifest = Path(args.out_dir) / "ranking.json"
            if not manifest.exists():
                print(f"找不到 {manifest},请先 render --edition 或指定 --hash", file=sys.stderr)
                return 1
            data = json.loads(manifest.read_text(encoding="utf-8"))
            row = next((x for x in data.get("items", []) if x.get("n") == args.n), None)
            if not row:
                print(f"编号 F{args.n:02d} 不在 {manifest}", file=sys.stderr)
                return 1
            content_hash = row["hash"]
        if not content_hash:
            print("需要 --hash 或 --n", file=sys.stderr)
            return 1
        store.record_feedback(content_hash, args.edition, args.label)
        it = store.get_item(content_hash)
        title = it.title if it else content_hash
        print(f"feedback edition={args.edition} label={args.label} {title}")
        return 0
    finally:
        store.close()


def cmd_ab(args: argparse.Namespace) -> int:
    """热度排序 vs 打分函数,写出对照稿,不标记 used_in。"""
    from pipeline.rank import collect_rank_candidates, heat_only_order, rank_items
    from render.ranked import render_ab_markdown, render_headline

    store = Store(args.db)
    try:
        items = collect_rank_candidates(store, window_hours=args.window_hours)
        heat = heat_only_order(items, n=args.limit)
        scored = rank_items(items, kind=args.kind, write_store=None)
        settings = load_settings()
        dest = Path(args.out_dir) if args.out_dir else (
            settings.editions_dir / f"_ab-{args.kind}"
        )
        dest.mkdir(parents=True, exist_ok=True)
        heat_md = "# 对照 A · 纯热度\n\n" + "\n".join(
            f"{i}. {it.title} · {it.source.value}" for i, it in enumerate(heat, 1)
        ) + "\n"
        scored_md = render_headline(scored, edition_id=f"ab-{args.kind}")
        if scored.deepread:
            from render.ranked import render_deepread

            scored_md += "\n" + render_deepread(scored, edition_id=f"ab-{args.kind}")
        (dest / "heat.md").write_text(heat_md, encoding="utf-8")
        (dest / "scored.md").write_text(scored_md, encoding="utf-8")
        cmp = render_ab_markdown(
            edition_id=f"ab-{args.kind}",
            heat_items=heat,
            heat_titles=[it.title for it in heat[: args.limit]],
            scored_titles=[ri.item.title for ri in scored.ranked[: args.limit]],
        )
        (dest / "compare.md").write_text(cmp, encoding="utf-8")
        print(f"wrote {dest / 'compare.md'}")
        print(f"candidates={len(items)} llm_calls={scored.n_llm} (budget 150)")
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    names = ", ".join(list_collector_names(include_dummy=True))

    parser = argparse.ArgumentParser(
        prog="main.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fishnet 个人情报报纸的命令行入口。\n"
            "Lab 0: dummy 幂等验收。\n"
            "Lab 1: DailyHotApi 热榜采集 → newly_entered → hotlist.md。\n"
            "Lab 3: RSSHub 订阅采集 → subscriptions.md。\n"
            "Lab 5: trafilatura 正文抽取 → items.content。\n"
            "Lab 6: APScheduler 常驻 + 早晚出报 + 系统体检。\n"
            "Lab 7: 收藏夹画像 + 两阶段打分 + 事件折叠 + 反馈。\n"
            "Lab 8: digest.md → A3 矩阵 HTML/PDF。"
        ),
        epilog=(
            "Lab 3 快速验收:\n"
            "  1) docker compose up -d          # 起 RSSHub(:1200) + redis\n"
            "  2) uv run main.py collect --only-rss\n"
            "  3) uv run main.py stats\n"
            "  4) uv run main.py render --section subscriptions\n"
            f"  RSSHub 基址(settings): {settings.rsshub_url}\n"
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
            "默认读取 config/sources.yaml 里的热榜 + feeds 订阅。\n"
            "入库 / 快照 / collector_runs 由 run_collector 统一处理。"
        ),
        epilog=(
            "示例:\n"
            "  uv run main.py collect --only hotlist_weibo\n"
            "  uv run main.py collect --only-rss\n"
            "  uv run main.py collect --only-hotlist\n"
            "  uv run main.py collect --only-targeted\n"
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
        "--only-rss",
        action="store_true",
        help="只跑 feeds 段的 RSS 订阅采集器(Lab 3)",
    )
    p_collect.add_argument(
        "--only-hotlist",
        action="store_true",
        help="只跑热榜采集器(Lab 1),跳过 RSS",
    )
    p_collect.add_argument(
        "--only-targeted",
        action="store_true",
        help="只跑 Lab 4 定向采集(小红书创作者;默认 collect 不会跑它)",
    )
    p_collect.add_argument(
        "--include-dummy",
        action="store_true",
        help="在跑全部时,额外包含 Lab 0 的 dummy 采集器",
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
        help="渲染报纸片段,或 --edition am/pm 出一期完整 digest",
        description=(
            "支持:\n"
            "  --section hotlist        Lab 1 新上榜 Top N(调试,不标记 used_in)\n"
            "  --section subscriptions  Lab 3 订阅更新(调试,不标记 used_in)\n"
            "  --section all            两者都写\n"
            "  --edition am|pm          Lab 6/7/8 出一期报纸(含个性化版面 + A3 PDF),标记 used_in"
        ),
    )
    p_render.add_argument(
        "--section",
        default="hotlist",
        help="要渲染的版面(hotlist / subscriptions / all;默认 hotlist)",
    )
    p_render.add_argument(
        "--window-hours",
        type=int,
        default=6,
        help="hotlist「新上榜」时间窗小时数(默认 6)",
    )
    p_render.add_argument(
        "--limit",
        type=int,
        default=20,
        help="hotlist Top N(默认 20)",
    )
    p_render.add_argument(
        "--sub-window-hours",
        type=int,
        default=48,
        help="subscriptions 时间窗小时数(默认 48)",
    )
    p_render.add_argument(
        "--sub-limit",
        type=int,
        default=40,
        help="subscriptions 展示条数(默认 40)",
    )
    p_render.add_argument(
        "--edition",
        choices=("am", "pm"),
        help="出一期早报(am)或晚报(pm)。与 --section 互斥,会标记 used_in",
    )
    p_render.add_argument(
        "--out-dir",
        type=Path,
        help="edition 输出目录(默认 data/editions/{期号})",
    )
    p_render.set_defaults(func=cmd_render)

    p_pdf = sub.add_parser(
        "pdf",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="把已有 digest 排成 A3 报纸(Lab 8,不标记 used_in)",
        description=(
            "只吃 data/editions/{期号}/ 里的 Markdown,写出 digest.html / digest.pdf / layout.json。\n"
            "排版调试用这个,不要重跑 collect。"
        ),
        epilog=(
            "示例:\n"
            "  uv run main.py pdf\n"
            "  uv run main.py pdf --edition 2026-08-26-am\n"
            "  uv run main.py pdf --dir data/editions/2026-08-26-am"
        ),
    )
    p_pdf.add_argument("--edition", help="期号,例如 2026-08-26-am")
    p_pdf.add_argument("--dir", type=Path, dest="dir", help="期次目录")
    p_pdf.add_argument(
        "--html-only",
        action="store_true",
        help="只写 HTML,不写 PDF",
    )
    p_pdf.set_defaults(func=cmd_pdf)

    p_enrich = sub.add_parser(
        "enrich",
        help="抽取正文并回填 items.content(Lab 5)",
        description=(
            "列出 B 站白名单视频(不转写),再对库里尚无 content 的网页条目抽正文。\n"
            "播放页不走 HTML 抽取。同一 URL 24h 内走 HTML 缓存;遵守 robots.txt。"
        ),
    )
    p_enrich.add_argument(
        "--limit",
        type=int,
        default=20,
        help="最多处理多少条缺正文的条目(默认 20)",
    )
    p_enrich.set_defaults(func=cmd_enrich)

    p_tx = sub.add_parser(
        "transcript",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="手动转写一条 B 站口播(下载+火山STT+改稿)",
        description=(
            "对一条 BV 下载音频、火山识别、Flash 改成见报稿。\n"
            "文件写到 data/transcripts/。若该 BV 已由 enrich 白名单入库，再写入 items.content。\n"
            "合集滴灌见报走 `render --edition am`，不会一次转写整个合集。"
        ),
        epilog=(
            "需要本机 ffmpeg，以及 .env 里的 STT_API_KEY 与 FISHNET_LLM_API_KEY。\n"
            "示例:\n"
            "  uv run main.py transcript BV19d4y1D7n3\n"
            "  uv run main.py transcript https://www.bilibili.com/video/BV19d4y1D7n3\n"
            "  uv run main.py transcript BV19d4y1D7n3 --from-text path/to.txt"
        ),
    )
    p_tx.add_argument("video", help="BV 号或 B 站播放页 URL")
    p_tx.add_argument(
        "--from-text",
        type=Path,
        help="跳过下载和识别，直接用这份转写做改稿",
    )
    p_tx.set_defaults(func=cmd_transcript)

    p_health = sub.add_parser(
        "health",
        help="系统体检(Lab 6)",
        description=(
            "检查过去 24h 从未成功的采集器、产出骤降、数据库大小、最老未处理数据。\n"
            "与报纸最后一页「系统体检」是同一份报告。"
        ),
    )
    p_health.set_defaults(func=cmd_health)

    p_serve = sub.add_parser(
        "serve",
        help="启动常驻调度(Lab 6)",
        description=(
            "APScheduler:热榜 30min / RSS 60min / 定向与正文 6h;\n"
            "每天 07:00 早报、18:00 晚报(Asia/Shanghai)。\n"
            "Ctrl-C 退出。手动出报请用 render --edition,不必等 cron。"
        ),
    )
    p_serve.add_argument(
        "--no-targeted",
        action="store_true",
        help="不调度 Lab 4 定向采集(默认:已配置 creator_id 的才会挂上)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_console = sub.add_parser(
        "console",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="本机网页控制台，管理 RSS 订阅源",
        description=(
            "打开 http://127.0.0.1:8787 ，粘贴知乎主页或公众号文章链接即可写入 YAML。\n"
            "只绑 localhost。内置 sources.yaml 不会被改写，新源进 overlay.yaml / wechat.yaml。"
        ),
        epilog=(
            "示例:\n"
            "  uv run main.py console\n"
            "  uv run main.py console --port 8788"
        ),
    )
    p_console.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认 127.0.0.1）",
    )
    p_console.add_argument(
        "--port",
        type=int,
        default=8787,
        help="端口（默认 8787）",
    )
    p_console.set_defaults(func=cmd_console)

    p_push = sub.add_parser(
        "push",
        help="推送报纸(Lab 9 占位)",
        description="Lab 9 再实现邮件 / Telegram 推送。",
    )
    p_push.set_defaults(func=cmd_push)

    p_golden = sub.add_parser(
        "golden",
        help="拟合收藏夹画像(Lab 7)",
        description=(
            "用 config 里的黄金集(≥50 篇 seed)拟合多簇 TasteProfile。\n"
            "--refresh 会按 golden.yaml 的收藏夹 id 经 RSSHub 追加。"
        ),
    )
    p_golden.add_argument(
        "--refresh",
        action="store_true",
        help="从 RSSHub 拉知乎收藏夹并追加到 config/golden.jsonl",
    )
    p_golden.add_argument(
        "--min-docs",
        type=int,
        default=50,
        help="验收线,默认 50",
    )
    p_golden.set_defaults(func=cmd_golden)

    p_fb = sub.add_parser(
        "feedback",
        help="给报纸条目打有用/无用(Lab 7)",
        description="读完 F01 之后: feedback --edition 2026-08-25-am --n 1 --label 1",
    )
    p_fb.add_argument("--edition", required=True, help="期号,例如 2026-08-25-am")
    p_fb.add_argument("--n", type=int, help="版面上的编号 Fnn")
    p_fb.add_argument("--hash", dest="hash", help="直接指定 content_hash")
    p_fb.add_argument(
        "--label",
        type=int,
        required=True,
        choices=(1, -1),
        help="1=有用, -1=无用",
    )
    p_fb.add_argument("--out-dir", type=Path, help="ranking.json 所在目录(默认 editions/期号)")
    p_fb.set_defaults(func=cmd_feedback)

    p_ab = sub.add_parser(
        "ab",
        help="热度 vs 打分 A/B 对照(Lab 7,不标记 used_in)",
        description="写出 heat.md / scored.md / compare.md,供自己盲评哪期更想读。",
    )
    p_ab.add_argument("--kind", choices=("am", "pm"), default="am")
    p_ab.add_argument("--out-dir", type=Path)
    p_ab.add_argument("--window-hours", type=int, default=48)
    p_ab.add_argument("--limit", type=int, default=20)
    p_ab.set_defaults(func=cmd_ab)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
