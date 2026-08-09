"""Lab 1 验收测试。

覆盖:
  - 热度/时间戳解析
  - newly_entered / fast_rising 逻辑(内存 SQLite,不依赖外网)
  - hotlist.md 渲染
  - (可选) 对本地 DailyHotApi 的连通性冒烟

长时间连跑请用: python -m tests.test_lab1_endurance --hours 6
短冒烟请用:     python -m tests.test_lab1_endurance --minutes 3
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.hotlist_generic import HotlistCollector, _parse_ts, _to_float
from core.base import run_collector
from core.schema import Item, Kind, Source
from core.settings import Settings
from core.store import Store
from render.hotlist import render_hotlist_md, write_hotlist_section

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


# ------------------------------------------------------------------ helpers
class FakeHotlist(HotlistCollector):
    """不打网络,按预设 rows 产出。"""

    def __init__(self, board: str, source: Source, rows: list[dict]):
        super().__init__(board, source, settings=Settings(dailyhot_url="http://fake"))
        self._rows = rows

    def collect(self):
        for i, row in enumerate(self._rows, start=1):
            yield Item(
                source=self.source,
                kind=Kind.HOTLIST,
                title=row["title"],
                url=row.get("url", f"https://example.com/{self.board}/{i}"),
                heat=_to_float(row.get("hot")),
                rank=i,
                collector=self.name,
                raw=row,
            )


def _seed_snapshots(store: Store, board: str, timeline: list[tuple[str, list[tuple[str, int]]]]):
    """timeline: [(observed_at_iso, [(hash, rank), ...]), ...]"""
    with store.tx() as cur:
        for observed_at, rows in timeline:
            for h, rank in rows:
                cur.execute(
                    "INSERT OR IGNORE INTO rank_snapshots VALUES (?,?,?,?,?)",
                    (h, board, rank, None, observed_at),
                )
                cur.execute(
                    "INSERT OR IGNORE INTO items "
                    "(content_hash, source, kind, title, url, fetched_at, collector) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (h, "weibo", "hotlist", f"t-{h}", f"https://e/{h}",
                     observed_at, f"hotlist_{board}"),
                )


# ================================================================== unit
print("\n[Lab 1] 解析工具")
check("热度 123.4万", _to_float("123.4万") == 1234000.0)
check("热度 2亿", _to_float("2亿") == 2e8)
check("热度 int", _to_float(42) == 42.0)
check("热度空", _to_float(None) is None)
check("时间戳秒", _parse_ts(1_700_000_000).year == 2023)
check("时间戳毫秒", abs(_parse_ts(1_700_000_000_000).timestamp() - 1_700_000_000) < 1)
check("离谱时间戳不炸", _parse_ts(7_670_000_000_000_000_000) is None)


print("\n[Lab 1] newly_entered / fast_rising")
tmp = tempfile.mkdtemp()
st = Store(os.path.join(tmp, "lab1.db"))
now = datetime.now(timezone.utc)
t_old = (now - timedelta(hours=10)).isoformat()
t_mid = (now - timedelta(hours=3)).isoformat()
t_new = (now - timedelta(minutes=10)).isoformat()

# A 在窗口前就有 → 不算新上榜
# B 只在窗口内出现 → 新上榜
# C 窗口内从 40 蹿到 5 → Δr=35
_seed_snapshots(st, "weibo", [
    (t_old, [("hashA", 1)]),
    (t_mid, [("hashA", 2), ("hashB", 30), ("hashC", 40)]),
    (t_new, [("hashA", 3), ("hashB", 28), ("hashC", 5)]),
])

entered = set(st.newly_entered("weibo", window_hours=6))
check("窗口前已存在的不算新上榜", "hashA" not in entered, str(entered))
check("窗口内首次出现算新上榜", "hashB" in entered and "hashC" in entered, str(entered))

rising = dict(st.fast_rising("weibo", window_hours=6, min_delta=15))
check("蹿升 Δr=35 被检出", rising.get("hashC") == 35, str(rising))
check("未达阈值不进蹿升榜", "hashB" not in rising, str(rising))


print("\n[Lab 1] Fake collector 入库 + 快照")
rows = [
    {"title": "话题一", "url": "https://weibo.com/1", "hot": "100万"},
    {"title": "话题二", "url": "https://weibo.com/2", "hot": "50万"},
]
c = FakeHotlist("demo", Source.WEIBO, rows)
new, dup = run_collector(c, st)
check("假榜首次入库全是 new", new == 2 and dup == 0, f"new={new} dup={dup}")
new2, dup2 = run_collector(c, st)
check("假榜二次幂等", new2 == 0 and dup2 == 2, f"new={new2} dup={dup2}")
snaps = st._conn.execute(
    "SELECT COUNT(*) FROM rank_snapshots WHERE board='demo'"
).fetchone()[0]
check("每次采集都写快照", snaps == 4, str(snaps))  # 2 items * 2 runs


print("\n[Lab 1] hotlist.md 渲染")
md = render_hotlist_md([
    Item(Source.WEIBO, Kind.HOTLIST, "标题|含竖线", "https://a.com", heat=1.2e6, rank=1),
])
check("Markdown 含表头", "| # | 来源 |" in md)
check("竖线被转义", "标题\\|含竖线" in md)
out = write_hotlist_section(st, ["weibo", "demo"], out_path=Path(tmp) / "hotlist.md")
check("写出文件非空", out.exists() and out.stat().st_size > 20, str(out))


print("\n[Lab 1] DailyHotApi 连通性(可选,需本机 :6688)")
import httpx

api = os.environ.get("DAILYHOT_URL", "http://127.0.0.1:6688")
probe_boards = ["zhihu", "bilibili", "douyin", "toutiao", "thepaper", "weibo"]
ok_boards = []
try:
    for b in probe_boards:
        r = httpx.get(f"{api}/{b}", timeout=20)
        if r.status_code == 200 and len(r.json().get("data") or []) > 0:
            ok_boards.append(b)
    check(
        f"本地 API 至少 5 个榜有数据(got {len(ok_boards)}: {ok_boards})",
        len(ok_boards) >= 5,
        f"ok={ok_boards}",
    )
except Exception as e:
    check("本地 API 至少 5 个榜有数据", False, repr(e))
    print("  hint: docker run -d --name dailyhot -p 6688:6688 "
          "-e ALLOWED_DOMAIN='*' -e ALLOWED_HOST=0.0.0.0 imsyy/dailyhot-api:latest")

st.close()
print(f"\n{'=' * 60}\n  PASSED {PASS}   FAILED {FAIL}\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
