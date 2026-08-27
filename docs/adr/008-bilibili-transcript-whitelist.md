# ADR-008 · B 站口播白名单：发现、合集滴灌、口播栏见报

- **状态**: 已接受
- **日期**: 2026-08-27
- **背景**: 报纸不定个人 B 站 UP，因为视频没有网页正文、进不了打分。要进主项目，必须先能**稳定列出**要转写的 BV，再按天印到报纸上，而不是一上来把 bili2text 整仓搬进来。

## 决策

1. **发现和转写拆开。** 和 [ADR-004](./004-extract-and-robots.md) 一样：先知道「有哪些片」，再决定抽哪一条的音频。`enrich` 只负责发现；默认不转写整集合集。出报时 `prepare_oral` 才转当天那一条。手动 `uv run main.py transcript <BV>` 仍可用。
2. **白名单只有两类 URL**，写在 `config/bilibili.yaml`：
  - **UP 主页** `https://space.bilibili.com/{mid}` → 本地 RSSHub `/bilibili/user/video/{mid}`。空间投稿接口有风控（`-352` / `-799` / 412），不直打 `arc/search`。RSSHub 给的是**最近几十条**，够轮询新片，不保证一次拉完历史上全部投稿。
  - **合集** `https://space.bilibili.com/{mid}/lists/{sid}?type=season` → 直打 `https://api.bilibili.com/x/polymer/web-space/seasons_archives_list`（`Referer`/`Origin` 带 B 站）。`type=series` 是另一套接口，不混用。
3. **合集先存全量 BV 目录。** 每次 enrich 把合集分页拉完，落到 `data/bilibili_seasons/{season_id}.json`。条目以 `Kind.VIDEO` 幂等入库，**不写 content**。HTML 抽取跳过 `bilibili.com/video`。
4. **合集按天滴灌，且必须见报。** 游标在 `core/drip.py`（`data/drip/{queue_id}.json`），与 B 站目录解耦，以后书的章节可以复用。每个 enabled 合集**每个早报 peek 一条**，写入 `04_oral.md` 并进 `digest.md` 之后才 `advance`。转写成功不算成功；没印上报纸游标不动。片失效才跳过。晚报不滴灌。
5. **VIDEO 仍不进 Lab 7 打分。** 口播是独立栏 `04_oral.md`，长稿不当头版 8 条里的一条。RSS 订阅版继续跳过视频。
6. **转写不进 collector，也不进 bili2text。** yt-dlp 只抽音轨 → 火山 STT → Flash 改稿。封面 1–2 张来自合集 `pic` / yt-dlp thumbnail，不下完整视频。

## 明确不做

- 不把合集 43 条第一次 enrich 就全部转写。
- 不订「B 站每周必看」这类热门榜。
- 不把官方字幕当正文（要登录，且不可靠）。
- 不把口播混进 `06_subscribe`。

## 后果

- **正面**: 合集能一次对齐 `meta.total`；UP 能从 RSSHub 拿到最新稿；早报固定有口播栏，滴灌不会在转写后悄无声息地丢稿。
- **负面**: UP 历史全量仍受 RSSHub/风控限制；合集目录会随 UP 增删稿件变化；早报可能因 STT 多等几分钟。
- **合规**: 只拉已声明的 UP / 合集；自用、低频。音频下载仍走公开播放地址。

## 何时重新评估

1. 读书滴灌：给章节列表实现 `list[DripUnit]` 即可，不要把游标写回 B 站目录。
2. RSSHub 对 UP 投稿长期 412 → 再考虑带 cookie 的空间接口，而不是回到无签名 `arc/search`。
3. 真要「一期报纸学完整个合集」→ 关掉滴灌，改成专刊，不改默认 enrich。
