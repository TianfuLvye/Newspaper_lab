# ADR-008 · B 站口播白名单：发现、合集目录、以后每天一条

- **状态**: 已接受
- **日期**: 2026-08-27
- **背景**: 报纸不定个人 B 站 UP，因为视频没有网页正文、进不了打分。沙箱里已经能下载音频 → 火山 STT → 改成可读稿。要进主项目，必须先能**稳定列出**要转写的 BV，而不是一上来把 bili2text 整仓搬进来。

## 决策

1. **发现和转写拆开。** 和 [ADR-004](./004-extract-and-robots.md) 一样：先知道「有哪些片」，再决定抽哪一条的音频。`enrich` 只负责发现；转写走手动 `uv run main.py transcript`，不进默认 enrich。
2. **白名单只有两类 URL**，写在 `config/bilibili.yaml`：
  - **UP 主页** `https://space.bilibili.com/{mid}` → 本地 RSSHub `/bilibili/user/video/{mid}`。空间投稿接口有风控（`-352` / `-799` / 412），不直打 `arc/search`。RSSHub 给的是**最近几十条**，够轮询新片，不保证一次拉完历史上全部投稿。
  - **合集** `https://space.bilibili.com/{mid}/lists/{sid}?type=season` → 直打 `https://api.bilibili.com/x/polymer/web-space/seasons_archives_list`（`Referer`/`Origin` 带 B 站）。`type=series` 是另一套接口，不混用。
3. **合集先存全量 BV 目录。** 每次 enrich 把合集分页拉完，落到 `data/bilibili_seasons/{season_id}.json`（标题、BV、时长、发布时间、`drip_index`）。条目同时以 `Kind.VIDEO` 幂等入库，**不写 content**。HTML 抽取跳过 `bilibili.com/video`，避免拿播放页空壳浪费额度。
4. **合集以后按天滴灌（未做）。** 订阅一个合集学东西时：先把该合集全部 BV 存下来，然后**每期报纸只扒其中一条**（按发布时间从旧到新，或按合集顺序），转写后见报。目录里预留 `drip.enabled` / `drip_index`。未打开时只更新目录，不下载、不 STT。
5. **VIDEO 仍不进 Lab 7 打分。** 口播见报要另开栏目（或专页），长稿不当头版 8 条里的一条。转写完成前，这些 Item 只在库里待命。
6. **转写不进 collector，也不进 bili2text。** 手动命令 `uv run main.py transcript <BV>`：yt-dlp 下音频 → 火山 STT → Flash 改稿。主依赖仍是 httpx + RSSHub + yt-dlp；Whisper/torch/bili2text 不进仓。`enrich` 只列 BV、不转写。



## 明确不做

- 不把合集 43 条第一次 enrich 就全部转写。
- 不订「B 站每周必看」这类热门榜。
- 不把官方字幕当正文（要登录，且不可靠）。

## 后果

- **正面**: 合集能一次对齐 `meta.total`（已用「硬核烹饪指南」43 条验证）；UP 能从 RSSHub 拿到最新稿（大问题 Dialectic）。滴灌以后有稳定游标，不必每次扫全站。
- **负面**: UP 历史全量仍受 RSSHub/风控限制；合集目录会随 UP 增删稿件变化，滴灌游标要能跳过已删 BV。
- **合规**: 只拉已声明的 UP / 合集；自用、低频。音频下载仍走公开播放地址，与沙箱试跑相同。



## 何时重新评估

1. **口播改稿已接到手动 `transcript` 命令**（可写入 `items.content`）。下一步是滴灌调度（每天一条）和口播版面；默认 `enrich` 仍不转写整集合集。
2. RSSHub 对 UP 投稿长期 412 → 再考虑带 cookie 的空间接口，而不是回到无签名 `arc/search`。
3. 真要「一期报纸学完整个合集」→ 关掉滴灌，改成专刊，不改默认 enrich。

