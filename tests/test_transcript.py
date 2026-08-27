"""口播转写：并节打包、BV 解析、下载头、入库回写。不访问外网。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.schema import Item, Kind, Source
from core.store import Store
from enrich.transcript import parse_bvid, video_url
from enrich.transcript_copy import (
    POLISH_BATCH,
    Sec,
    char_count,
    extract_json_obj,
    merge_slices,
    pack_section_jobs,
    parse_layout,
    parse_layout_sections,
    render_long,
    split_sentences,
)
from enrich.transcript_download import BILI_HTTP_HEADERS, bilibili_ydl_opts


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def test_parse_bvid():
    check("bare id", parse_bvid("BV19d4y1D7n3") == "BV19d4y1D7n3")
    check(
        "url",
        parse_bvid("https://www.bilibili.com/video/BV19d4y1D7n3?spm_id_from=x")
        == "BV19d4y1D7n3",
    )
    try:
        parse_bvid("https://example.com/")
        check("reject non-bv", False)
    except ValueError:
        check("reject non-bv", True)
    check("watch url", video_url("BV19d4y1D7n3").endswith("/BV19d4y1D7n3"))


def test_pack_section_jobs():
    intro = Sec(0, "", ["甲" * 348])
    small = [
        Sec(i, f"## 节{i}", ["乙" * n])
        for i, n in enumerate([1007, 365, 169, 367, 292, 370, 275, 604], start=1)
    ]
    secs = [intro, *small]
    total = sum(s.chars for s in secs)
    check("cooking-size under batch", total < POLISH_BATCH, str(total))
    jobs = pack_section_jobs(secs)
    check("one job for 9 sections", len(jobs) == 1 and len(jobs[0]) == 9)

    big = Sec(0, "## big", ["x" * 2500, "y" * 2500])
    tiny = Sec(1, "## small", ["z" * 100])
    jobs2 = pack_section_jobs([tiny, big])
    check("oversize not mixed", jobs2[0][0].heading == "## small" and len(jobs2) == 3)
    check("oversize split paras", all(j[0].heading == "## big" for j in jobs2[1:]))

    a = Sec(0, "## h", ["p1"])
    b = Sec(0, "## h", ["p2"])
    c = Sec(1, "## i", ["p3"])
    merged = merge_slices([a, b, c])
    check("merge same sid", len(merged) == 2 and merged[0].paras == ["p1", "p2"])


def test_layout_roundtrip():
    text = "第一句。第二句。第三句。"
    sents = split_sentences(text)
    check("three sentences", sents == ["第一句。", "第二句。", "第三句。"])
    raw = '{"guide":"导读八十到一百二十字的占位。","paragraph_before":[3],"sections":[{"before":2,"title":"第二节"}]}'
    layout = parse_layout(raw, 3)
    md = render_long(sentences=sents, title="题", layout=layout)
    check("has h1 and h2", md.startswith("# 题") and "## 第二节" in md)
    secs = parse_layout_sections(md.split("\n\n", 2)[2])
    check("intro then section", secs[0].heading == "" and secs[1].heading == "## 第二节")
    data = extract_json_obj('noise {"sections":[{"heading":"","paragraphs":["a"]}]} tail')
    check("json extract", data["sections"][0]["paragraphs"] == ["a"])
    check("char_count strips space", char_count("a b\n") == 2)


def test_download_headers():
    opts = bilibili_ydl_opts(Path(tempfile.mkdtemp()) / "dl")
    headers = opts["http_headers"]
    check("referer", headers["Referer"] == BILI_HTTP_HEADERS["Referer"])
    check("origin", headers["Origin"] == "https://www.bilibili.com")
    check("audio only", opts["format"] == "bestaudio/best")
    check("ffmpeg extract", opts["postprocessors"][0]["preferredcodec"] == "m4a")


def test_store_write_content():
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    store = Store(tmp)
    try:
        it = Item(
            source=Source.BILIBILI,
            kind=Kind.VIDEO,
            title="盐",
            url="https://www.bilibili.com/video/BV19d4y1D7n3",
            collector="bilibili_whitelist",
        )
        store.upsert_items([it])
        found = store.find_bilibili_video("BV19d4y1D7n3")
        check("find by bvid", found is not None and found.title == "盐")
        store.update_content(found.content_hash, "# 见报稿\n\n正文。\n")
        again = store.get_item(found.content_hash)
        check("content saved", (again.content or "").startswith("# 见报稿"))
        check("missing bvid", store.find_bilibili_video("BVnotexist") is None)
    finally:
        store.close()


def main() -> int:
    test_parse_bvid()
    test_pack_section_jobs()
    test_layout_roundtrip()
    test_download_headers()
    test_store_write_content()
    print("all transcript checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
