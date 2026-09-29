# 06: 黄金用例验收与使用文档

**What to build:** 用真实查询跑通证据层端到端流程并按验收标准抽查，同时补齐使用文档：配置项、开关用法、产物说明、规则表维护与等级缓存提拔流程。完成后本阶段可交付领导试用。

**Blocked by:** 03（顶层接入；可与 05 并行）

**Status:** done

- [x] 真实查询（配置的检索器与 LLM）端到端跑通，证据产物落盘（JSON + Markdown）
- [x] 抽查 20 条证据条的原文引用逐字命中率 100%
- [x] 来源等级与规则表一致；LLM 兜底与默认分级的标记正确、可解释
- [x] 成本统计包含证据抽取阶段
- [x] 使用文档（在仓库既有文档位置）：配置项说明、开关用法、产物结构说明、规则表维护与等级缓存提拔流程
- [x] （可选）顺带验证定向域名搜索（依赖 04）

## Comments

2026-09-29 黄金用例实跑（配置的博查检索器 + 网关 LLM）：

```
python scripts/evidence_golden_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
summary: sources=10 scraped=10 evidence=20 rejected=11 by_tier={A:0, B:0, C:7, D:3}
[2] 引用逐字命中: 20/20 (要求 100%)
[3] 来源等级与判定方式: 10 个来源已校验
[1] 产物落盘: outputs/research_503ece275743.evidence.json | .evidence.md
[4] 成本: 总计 $0.4190，证据抽取阶段 $0.3995
PASS
```

定向域名搜索实跑（博查）：`site:stats.gov.cn` 被剥离出查询并转为 `include=stats.gov.cn`，返回 3 条结果全部来自 stats.gov.cn。

说明：验收中 11 条 `rejected` 均为网关 LLM 偶发空响应导致的 `parse_failed`（fail-closed 丢弃，符合设计）；使用文档落位 `docs/docs/gpt-researcher/gptr/evidence-layer.md` 并接入 sidebars 与 `.env.example`。
