# ADR-001 · 为何不把 TrendRadar 当作 collector 数据源

- **状态**: Accepted
- **日期**: 2026-08-09
- **Lab**: Lab 2
- **决策者**: Fishnet / Newspaper_lab

## 背景

Lab 2 要求在两种路线里做取舍：

1. **直接用 TrendRadar 作为一个 collector 数据源**（跑它的 Docker / Actions，吃它的推送或落盘结果）
2. **只借鉴设计、自己实现**（关键词 DSL、增量检测思路进我们自己的 `pipeline/` + `store`）

TrendRadar 完成度很高：多平台热榜、RSS、多渠道推送、timeline 调度、AI 分析都齐。把它「接进来」看起来最快。

## 决策

**选择路线 2：只借鉴设计，不依赖 TrendRadar 运行时。**

具体借用：

| 借鉴项 | 落点 |
|---|---|
| must / any(普通词) / exclude | `pipeline/keyword.py` + `config/keywords.yaml` |
| 全局噪音过滤意识 | 每组强制积累 `exclude`；后续可加 global_exclude |
| 「新增才推送」的增量心智 | 已有 Lab 1 `newly_entered` / 快照表；关键词过滤叠在加工层 |
| 调度分区（采集 vs 推送） | 留给 Lab 6 的 APScheduler / CLI 可单独调用 |

不借用：把它的进程、推送管道、NewsNow 依赖链嵌进我们的 collector。

## 理由

1. **目标形态不同**  
   TrendRadar 产出推送卡片；我们要 PDF/Markdown 报纸 + 个性化排序。接它的输出还要二次建模，省不下多少，反而多一层阻抗。

2. **依赖链已经够长**  
   TrendRadar 热榜默认走 NewsNow：`平台 → NewsNow → TrendRadar → 我们`。可用性乘性衰减，故障归因也难。我们 Lab 1 已是 `平台 → DailyHotApi → 我们`（少一环）。再套 TrendRadar 是开倒车。

3. **采集与加工必须解耦（Lab 1.3 原则 1）**  
   若 collector 直接「要一份 TrendRadar 已过滤的结果」，过滤逻辑就锁死在上游；改版面/改关键词等于跟上游配置吵架。正确位置：我们的 collector 仍只捞原始热榜，`KeywordEngine` 在 pipeline 里收窄。

4. **数据契约已定**  
   全系统只认 `Item`。TrendRadar 的内部 schema / 推送 JSON 不是 `Item`，适配层会变成永久税。

5. **Lab 时间盒**  
   手册明确：别在 Lab 2 花超过两天。价值是参考，不是成为重度用户。

## 后果

- **正面**: 关键词与热榜采集可独立演进；少一环依赖；版面 `sections` 是我们独有、TrendRadar 没有的字段。
- **负面**: 推送渠道（飞书/TG）要 Lab 9 自己做；AI 分析/漂亮 HTML 报告暂不复用。
- **风险**: 关键词召回有上限（同义/黑话）——已用 `aliases` 打底，Lab 7 向量召回补洞。

## 什么情况下会推翻

仅当同时满足：

1. 我们决定**永久只要热榜监控卡片**，不做长文报纸；且  
2. 自建 DailyHot + 关键词维护成本显著高于维护一个 TrendRadar 实例。

当前愿景不满足第 1 条，故本 ADR 保持 Accepted。
