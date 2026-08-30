# ADR-009 · Lab 8 版心改为 newspaper-layout v0.4

- **状态**: 接受
- **Lab**: 8
- **日期**: 2026-08-30
- **取代**: [ADR-007](./007-newspaper-grid.md)

## 上下文

ADR-007 用自研 A3 4×8 网格 + reportlab 拼版。能印，但空洞多、模板单一、续页是整数格子凑出来的，观感不像报纸。

独立仓库 [TianfuLvye/newspaper-layout](https://github.com/TianfuLvye/newspaper-layout) v0.4 已经用同一期 `2026-08-28-am` 验过：Guardian 模板、Chromium 精确量字、真实「下转第 X 版 / 上接第 X 版」。

采集和打分仍然只产出 Markdown。缺的是把期次目录变成 v0.4 要的 `articles.json`。

## 决策

1. **版心外包给 v0.4**：`optimize-render --exact`，模板在 `render/newspaper_templates/`。
2. **本仓库只做转换**：`render/edition_to_articles.py` 读 `01_`…`99_` / `items/` / `ranking.json`，写出 `articles.json`。
3. **HTML 是权威输出**：`digest.html`。PDF 用同一套 Chromium 按 A3 打印，不再用 reportlab 画字。
4. **调试入口不变**：`uv run main.py pdf --edition …` 不打 `used_in`。

## 后果

- **正面**: 版面密度和续页由模板优化器负责；量字和渲染共用同一套 CSS。
- **负面**: 每天出报要跑 Chromium，比旧网格慢一个数量级。测量缓存在 `data/.cache/newspaper-measure.json`。
- **成本**: 依赖 `newspaper-layout`、`playwright`、本机 Chromium（`playwright install chromium` 或 `CHROMIUM_PATH`）。去掉 reportlab。

## 何时重新评估

1. 若要换模板集，只改 `render/newspaper_templates/`，不必动转换器。
2. 若家用机器跑不动 exact，可把 `render_newspaper(..., exact=False)` 当调试开关；续页仍需要 Chromium。
