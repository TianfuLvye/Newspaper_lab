# Lab 8 · 渲染:期次 Markdown → newspaper-layout A3 报纸

> **范围**: 把已有 `01_`…`99_` / `digest.md` 排成早餐能读的 HTML/PDF。不回头改采集和打分。  
> **决策**: 版心用 [newspaper-layout v0.4](https://github.com/TianfuLvye/newspaper-layout)，不再自研 4×8 网格。见 [ADR-009](./adr/009-newspaper-layout-v04.md)。旧网格决策见已 superseded 的 [ADR-007](./adr/007-newspaper-grid.md)。

## 本 Lab 完成了什么

1. **期次 → articles.json**: `render/edition_to_articles.py` 读分版 Markdown（订阅优先 `items/*.md`），去掉打分/反馈命令，补上 `kind` / `priority` / 图宽高。
2. **模板拼版**: Guardian 模板在 `render/newspaper_templates/`。Chromium 精确量字 + 真实续页（下转/上接）。
3. **双输出**: `digest.html` 是权威版面；`digest.pdf` 是同一份 HTML 的 A3 打印件。
4. **今日综述**: 仍由 `render/lede.py` 写 `00_lede.md`，再作为一篇 brief 进优化器。
5. **CLI**: `uv run main.py render --edition am` 出报时顺带排版；`uv run main.py pdf --edition 2026-08-28-am` 只排版、不打 `used_in`。

本机需要 Chromium：`uv run playwright install chromium`，或设置 `CHROMIUM_PATH`。测量缓存在 `data/.cache/newspaper-measure.json`。出刊单测设 `FISHNET_SKIP_LAYOUT=1`，只写 `articles.json`、不跑优化器。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 中文无乱码、标点正常 | v0.4 与最终 HTML 共用同一套 CSS；Chromium 量字 |
| ≥5 个版面 | 解析 `01_headline` / `02_hotlist` / `03_deepread` / `04_oral` / `06_subscribe` / `07_critical` / `99_health` |
| 早晚报有实质差异 | 报头文案不同；稿件 `id` 前缀 `am` / `pm` |
| 过长分页 | v0.4 `DOMSplitter` + `ContinuationAllocator`，印「下转第 X 版 / 上接第 X 版」 |
| 空版面比错误更伤 | 无数据栏目不硬塞低分稿；体检 `kind=system_report` 压到报纸末尾 |

## 模块与函数设计笔记

### `render/edition_to_articles.py` · `edition_to_articles`

- **目的**: 一期目录 → v0.4 `Article` JSON。
- **来源**: 分版文件优先；订阅正文优先用 `items/*.md`；只有 `digest.md` 时按 H1 栏目回退。
- **kind**: health → `system_report`；critical 长文 → `report`；综述/热榜目录 → `brief`；其余按字数 brief/normal/long。
- **priority**: headline 0.97 … health 0.18，驱动头版 lead 与末页报告。

### `render/newspaper.py` · `render_newspaper`

- **目的**: 写 `00_lede.md` → `articles.json` → 调 v0.4 optimize-render → `digest.html` / `layout.json` / `digest.pdf`。
- **为什么 PDF 失败只警告**: 出报契约仍是 Markdown；`used_in` 已经打上。HTML 还能用浏览器印。

### `render/parse_edition.py` / `render/lede.py`

- 内容解析和综述仍在本仓库。格子类型已从 `render/layout/model.py` 挪到 `render/edition_model.py`。

## 本地怎么验收

```bash
uv sync
uv run playwright install chromium
uv run python -m tests.test_lab8          # 转换器 + 模板加载,不跑全量拼版
uv run python -m tests.test_all

# 只排版,不重跑打分、不打 used_in
uv run main.py pdf --edition 2026-08-28-am
open data/editions/2026-08-28-am/digest.html
```

## 思考题备忘

1. **HTML 也出**: 早餐多半在手机上看。HTML 是扫读和权威版面，PDF 是归档/打印。
2. **只有 5 条**: 不凑数。空栏目不进 `articles.json`，体检仍见报。

## 留给下一 Lab 的接口

- `digest.pdf` 是邮件附件;`articles.json` 给摘要正文。`digest.html` 默认不附(打印 CSS,体积大)。
- `layout.json` 给调试,不必进 git。
- `uv run main.py push` 已接 SMTP;Telegram / 客户端仍跳过。
