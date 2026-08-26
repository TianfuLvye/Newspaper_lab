# ADR-006 · 向量后端:TF-IDF + SQLite,不上 chromadb

- **状态**: 接受
- **Lab**: 7
- **日期**: 2026-08-25

## 上下文

手册建议用 `sqlite-vec` 或 `chromadb` 存向量,「别自己撸暴力检索」。一期报纸的候选是几百到三千,黄金集是几十到几百。

## 决策

用汉字 n-gram TF-IDF(+截断 SVD)做 embedding,向量以 BLOB 放在同一份 SQLite 里,检索用 numpy 余弦。预留 `Embedder` 协议,以后要换 `bge-large-zh` 只换实现。

## 理由

1. **量级不对等**。chromadb 是给百万向量准备的服务。n=3000 时一次矩阵乘比 HTTP 打本地向量库还简单,还少一个会挂的进程。
2. **离线可测**。黄金集和单测必须不依赖模型下载和 API。TF-IDF 确定性、秒级拟合。
3. **sqlite-vec 在 macOS 上要加载扩展**,和「家用笔记本常驻」的运维预算不合;BLOB 已经在我们控制的 schema 里。
4. **语义召回的洞**用关键词 aliases(宇树/Unitree)先补。神经 embedding 是增强,不是本 Lab 能阻塞出报的依赖。

## 后果

- 同义词、黑话、英译中仍会漏,这是已知上限,和 ADR-001 里说的一致。
- 换模型时旧 BLOB 不能混用,所以表里存了 `model` 字段。
- 若以后候选到十万级,再评估 sqlite-vec,而不是现在提前交税。
