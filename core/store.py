"""存储层 —— Lab 0 标准答案。

设计要点:
- 幂等由主键 + INSERT OR IGNORE 保证,不靠先 SELECT 再 INSERT(有竞态)。
- WAL 模式:允许一个写者与多个读者并发,解决多 collector 并行时的
  `database is locked`。
- 所有时间以 ISO8601 UTC 字符串存储,SQLite 没有原生 datetime 类型。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.schema import Item, Source, Kind

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;

CREATE TABLE IF NOT EXISTS items (
    content_hash TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    summary      TEXT,
    content      TEXT,
    author       TEXT,
    author_id    TEXT,
    published_at TEXT,
    fetched_at   TEXT NOT NULL,
    rank         INTEGER,
    heat         REAL,
    tags         TEXT,
    collector    TEXT NOT NULL,
    score        REAL,
    cluster_id   TEXT,
    llm_summary  TEXT,
    used_in      TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_src     ON items(source, kind);
CREATE INDEX IF NOT EXISTS idx_items_used    ON items(used_in);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(used_in, fetched_at)
    WHERE used_in IS NULL;

CREATE TABLE IF NOT EXISTS raw_payloads (
    content_hash TEXT PRIMARY KEY,
    collector    TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    payload      TEXT NOT NULL
);

-- 热榜快照:同一条内容在不同时刻的名次,用于「新上榜 / 蹿升」检测。
-- 注意它是独立于 items 的时序表,items 只保留最后一次的 rank。
CREATE TABLE IF NOT EXISTS rank_snapshots (
    content_hash TEXT NOT NULL,
    board        TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    heat         REAL,
    observed_at  TEXT NOT NULL,
    PRIMARY KEY (content_hash, board, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_snap_board ON rank_snapshots(board, observed_at);

CREATE TABLE IF NOT EXISTS collector_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    collector   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    item_count  INTEGER DEFAULT 0,
    new_count   INTEGER DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs ON collector_runs(collector, started_at);

CREATE TABLE IF NOT EXISTS feedback (
    content_hash TEXT NOT NULL,
    edition      TEXT NOT NULL,
    label        INTEGER NOT NULL,   -- 1 有用 / -1 无用
    created_at   TEXT NOT NULL,
    PRIMARY KEY (content_hash, edition)
);
"""

CST = timezone(timedelta(hours=8))
_COLS = [
    "content_hash", "source", "kind", "title", "url", "summary", "content",
    "author", "author_id", "published_at", "fetched_at", "rank", "heat",
    "tags", "collector", "score", "cluster_id", "llm_summary", "used_in",
]


class Store:
    def __init__(self, path: str | Path = "data/fishnet.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)

    @contextmanager
    def tx(self):
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------ 写入
    def upsert_items(self, items: Iterable[Item]) -> tuple[int, int]:
        """返回 (新增数, 重复数)。

        关键:用 `changes()` 判断是否真的插入了,而不是先查后插。
        重复时只更新易变字段(rank/heat)和补齐缺失的 content。
        """
        new = dup = 0
        with self.tx() as cur:
            for it in items:
                row = it.to_row()
                cur.execute(
                    f"INSERT OR IGNORE INTO items ({','.join(_COLS)}) "
                    f"VALUES ({','.join('?' * len(_COLS))})",
                    [row[c] for c in _COLS],
                )
                if cur.rowcount == 1:
                    new += 1
                else:
                    dup += 1
                    # 重复条目:刷新热度,并在原来没有正文时补上
                    cur.execute(
                        "UPDATE items SET rank=?, heat=?, "
                        "content=COALESCE(content, ?), summary=COALESCE(summary, ?) "
                        "WHERE content_hash=?",
                        (it.rank, it.heat, it.content, it.summary, it.content_hash),
                    )
                if it.raw:
                    cur.execute(
                        "INSERT OR IGNORE INTO raw_payloads VALUES (?,?,?,?)",
                        (it.content_hash, it.collector, row["fetched_at"],
                         json.dumps(it.raw, ensure_ascii=False, default=str)),
                    )
        return new, dup

    def record_snapshot(self, items: Iterable[Item], board: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.tx() as cur:
            cur.executemany(
                "INSERT OR IGNORE INTO rank_snapshots VALUES (?,?,?,?,?)",
                [(i.content_hash, board, i.rank or 999, i.heat, now)
                 for i in items if i.rank is not None],
            )

    # ------------------------------------------------------------------ 运行记录
    def start_run(self, collector: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO collector_runs (collector, started_at, status) VALUES (?,?,?)",
            (collector, datetime.now(timezone.utc).isoformat(), "running"),
        )
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, item_count: int = 0,
                   new_count: int = 0, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE collector_runs SET finished_at=?, status=?, item_count=?, "
            "new_count=?, error=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), status, item_count,
             new_count, error, run_id),
        )

    # ------------------------------------------------------------------ 查询
    def query_items(self, since: datetime | None = None, kinds=None,
                    sources=None, unused_only: bool = True,
                    limit: int = 5000) -> list[Item]:
        sql = "SELECT * FROM items WHERE 1=1"
        args: list = []
        if since:
            sql += " AND fetched_at >= ?"
            args.append(since.astimezone(timezone.utc).isoformat())
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += [k.value if isinstance(k, Kind) else k for k in kinds]
        if sources:
            sql += f" AND source IN ({','.join('?' * len(sources))})"
            args += [s.value if isinstance(s, Source) else s for s in sources]
        if unused_only:
            sql += " AND used_in IS NULL"
        sql += " ORDER BY fetched_at DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_item(r) for r in self._conn.execute(sql, args)]

    def mark_used(self, hashes: list[str], edition: str) -> None:
        self._conn.executemany(
            "UPDATE items SET used_in=? WHERE content_hash=? AND used_in IS NULL",
            [(edition, h) for h in hashes],
        )

    def items_missing_content(self, limit: int = 50) -> list[Item]:
        """Lab 5:尚未抽出正文、但有 http(s) 链接的条目。"""
        rows = self._conn.execute(
            "SELECT * FROM items WHERE url LIKE 'http%' "
            "AND (content IS NULL OR trim(content) = '') "
            "ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def update_content(self, content_hash: str, content: str) -> None:
        """回填 items.content。空字符串视为无效,不写。"""
        text = (content or "").strip()
        if not text:
            return
        self._conn.execute(
            "UPDATE items SET content=? WHERE content_hash=?",
            (text, content_hash),
        )

    def get_item(self, content_hash: str) -> Item | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def newly_entered(self, board: str, window_hours: int = 6) -> list[str]:
        """窗口内首次出现的条目 hash。

        实现:在窗口内出现过,且在窗口之前从未出现过。
        """
        t0 = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        rows = self._conn.execute(
            "SELECT DISTINCT content_hash FROM rank_snapshots "
            "WHERE board=? AND observed_at >= ? AND content_hash NOT IN ("
            "  SELECT content_hash FROM rank_snapshots WHERE board=? AND observed_at < ?"
            ")", (board, t0, board, t0)).fetchall()
        return [r[0] for r in rows]

    def fast_rising(self, board: str, window_hours: int = 6,
                    min_delta: int = 15) -> list[tuple[str, int]]:
        r"""排名蹿升检测。

        \Delta r = r_first - min(r_t),窗口内首次名次减去最优名次。
        用最优名次而非最新名次,是为了捕捉「冲上去又掉下来」的爆点,
        这类内容往往正是当天最值得记录的。
        """
        t0 = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        rows = self._conn.execute("""
            SELECT content_hash,
                   (SELECT rank FROM rank_snapshots s2
                     WHERE s2.content_hash=s1.content_hash AND s2.board=s1.board
                       AND s2.observed_at >= ?
                     ORDER BY s2.observed_at ASC LIMIT 1) AS r_first,
                   MIN(rank) AS r_best
            FROM rank_snapshots s1
            WHERE board=? AND observed_at >= ?
            GROUP BY content_hash
        """, (t0, board, t0)).fetchall()
        out = [(r["content_hash"], r["r_first"] - r["r_best"]) for r in rows
               if r["r_first"] is not None and r["r_first"] - r["r_best"] >= min_delta]
        return sorted(out, key=lambda x: -x[1])

    def health(self, hours: int = 24) -> list[dict]:
        """系统体检:每个 collector 近 N 小时的成功率与产出量。"""
        t0 = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute("""
            SELECT collector,
                   COUNT(*) AS runs,
                   SUM(status='ok') AS ok_runs,
                   SUM(new_count) AS new_items,
                   MAX(started_at) AS last_run
            FROM collector_runs WHERE started_at >= ?
            GROUP BY collector ORDER BY collector
        """, (t0,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        c = self._conn.execute
        return {
            "items": c("SELECT COUNT(*) FROM items").fetchone()[0],
            "unused": c("SELECT COUNT(*) FROM items WHERE used_in IS NULL").fetchone()[0],
            "with_content": c("SELECT COUNT(*) FROM items WHERE content IS NOT NULL").fetchone()[0],
            "by_source": {r[0]: r[1] for r in
                          c("SELECT source, COUNT(*) FROM items GROUP BY source")},
        }

    # ------------------------------------------------------------------ helper
    @staticmethod
    def _row_to_item(r: sqlite3.Row) -> Item:
        it = Item(
            source=Source(r["source"]), kind=Kind(r["kind"]),
            title=r["title"], url=r["url"], summary=r["summary"],
            content=r["content"], author=r["author"], author_id=r["author_id"],
            published_at=datetime.fromisoformat(r["published_at"]) if r["published_at"] else None,
            fetched_at=datetime.fromisoformat(r["fetched_at"]),
            rank=r["rank"], heat=r["heat"],
            tags=json.loads(r["tags"]) if r["tags"] else [],
            collector=r["collector"], score=r["score"],
            cluster_id=r["cluster_id"], llm_summary=r["llm_summary"],
            used_in=r["used_in"], content_hash=r["content_hash"],
        )
        return it

    def close(self):
        self._conn.close()
