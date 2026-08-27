# Lab 8 · 渲染:Markdown → 一份 A3 矩阵报纸

> **范围**: 把已有 `digest.md` / `01_`…`99_` 排成早餐能读的 PDF。不回头改采集和打分。  
> **决策**: 不用手册 8.1 的四种「流式」方案当版心,自研 A3 网格矩形装箱。见 [ADR-007](./adr/007-newspaper-grid.md)。

## 本 Lab 完成了什么

1. **A3 矩阵排版**: 297×420 mm 切成 6 栏 × 14 行。每篇文章占一块矩形,不允许 L 形绕排。
2. **过长分页**: 正文量字后切成续页,写「下转第 N 版 / 上接第 M 版」。单篇最多 2–3 页,再长截断并指向 `items/`。
3. **配图**: `enrich/images.py` 从微信 / 华尔街见闻 / 知乎收候选,出报时启发式或 LLM 挑 1–3 张下载到 `images/`。`render/layout/images.py` 切图井;没有文件就不切框。
4. **双输出**: `digest.html`(手机扫读 + 浏览器打印兜底)和 `digest.pdf`(归档)。中间层 Markdown 不动。
5. **今日综述**: 头版报头里约 200 字。有 `FISHNET_LLM_API_KEY` 调一次 LLM,否则抽头版首句。
6. **CLI**: `uv run main.py render --edition am` 出报时顺带排版;`uv run main.py pdf --edition 2026-08-26-am` 只排版、不打 `used_in`。

## 对应 Lab 原则 / 验收点

| 验收 / 原则 | 落点 |
|---|---|
| 中文无乱码、标点正常 | 系统宋体/黑体 TTF 注册进 reportlab,自写 CJK 折行 |
| ≥5 个版面 | 解析 `01_headline` / `02_hotlist` / `03_deepread` / `06_subscribe` / `07_critical` / `99_health` |
| 早晚报有实质差异 | 早报字更密、热榜靠前;晚报字略大、深度靠前;报头文案不同 |
| 无孤行/断表 | 续页在句号/段末切开;剩余 <48 字并入本格 |
| digest.md → PDF < 30s | 装箱是整数格子,量字+reportlab 通常几秒 |
| 空版面比错误更伤 | 无数据栏目印占位块,不拿低分稿填 |

## 模块与函数设计笔记

### `render/layout/pack.py` · `MaxRects`

- **目的**: 在格子坐标里找「短边剩余最小」的矩形空洞。
- **为什么**: CSS `column-count` 和 Typst 流式栏是「字去找缝」;报纸是「先切块再倒字」。
- **刻意不做**: 不引入 bin-packing 库。n < 50,自己写 80 行更可测。

### `render/layout/images.py` · `plan_image_slots`

- **目的**: 文章矩形内切一块图井,正文仍是**一块矩形**。
- **为什么保持矩形**: L 形绕排会让分页和量字两套逻辑分叉,图还没稳定时不值得。
- **1/2/3 张**: 横图靠上、竖图靠左、三张走英雄+两小图;短边小于 28mm 改 cover,再小就 overflow 到续页。
- **真实文件**: Markdown `![说明](images/xxx.jpg)` 相对期次目录;PDF 按这个路径 `drawImage`,HTML 同源相对路径。候选来自 `Item.images`,出报时才下载。

### `render/layout/engine.py` · `layout_edition`

- **目的**: 文章 → 一串 `PlacedBlock`(页码 + 格子 + mm)。
- **头版**: 顶两行锁给报头+综述;内页一行报眉。
- **稀薄期**: 3 篇以下把体检提前,报头写警告。

### `render/newspaper.py` · `render_newspaper`

- **目的**: 一期目录 → `layout.json` + `digest.html` + `digest.pdf`。
- **为什么 PDF 失败只警告**: 出报的契约仍是 Markdown;`used_in` 已经打上。HTML 还能用浏览器印。

## 本地怎么验收

```bash
uv sync
uv run python -m tests.test_lab8
uv run python -m tests.test_all          # 旧 Lab 回归;出报会多写 pdf

# 只排版,不重跑打分、不打 used_in
uv run main.py pdf --edition 2026-08-26-am
open data/editions/2026-08-26-am/digest.pdf
open data/editions/2026-08-26-am/digest.html
```

## 思考题备忘

1. **HTML 也出**: 早餐多半在手机上看。HTML 是扫读格式,PDF 是归档/打印。分层正是 Markdown 中间层的回报。
2. **只有 5 条**: 不凑数。合并留白、体检提前、占位块写「今日无数据」。假装满版比空版更糟。

## 美术(对照华尔街日报)

- 白底;稿件之间用细竖线/横线,不画圆角卡片。
- 正文在稿件矩形里再切竖栏(约 41mm 一栏),两端对齐。
- 头版标题显著加大;报头是黑字刊名 + 双细线,不是色块。
- 头版页底通栏 **INSIDE** 条,提示内页标题和页码。右下角小盒会把 F02/F03 撕出一栏空洞,所以改成横条。
- 头版只放导语,续文下转内页,不在头版碎格里续摊。

## 留给下一 Lab 的接口

- `digest.pdf` / `digest.html` 是 Lab 9 邮件附件和正文的输入。
- `layout.json` 给调试,不必进 git。
- 推送仍是 `main.py push` 占位。
