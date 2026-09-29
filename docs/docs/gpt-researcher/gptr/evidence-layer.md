---
sidebar_position: 8
---

# 证据层（来源分级与证据表）

证据层（Evidence Layer）在研究完成时自动运行，把抓取到的每个**来源**转换成结构化**证据条目**（指标、数值、口径、**原文引用**、来源标识），并给每个来源按**发布主体**评定**来源等级**（A 政府 / B 权威机构与央媒 / C 行业与正规媒体 / D 自媒体及其他），产出可下载的**证据产物**（JSON + Markdown）。

设计决策见仓库 `docs/adr/`（ADR-0001 顶层触发、ADR-0002 规则即数据、ADR-0003 引用 fail-closed），术语见根目录 `CONTEXT.md`。

## 开启与配置

证据层默认关闭；关闭时现有研究行为与产物完全不变。通过环境变量或配置文件开启：

```bash
# .env
EVIDENCE_EXTRACTION_ENABLED=true
```

```python
import os
os.environ["EVIDENCE_EXTRACTION_ENABLED"] = "true"

from gpt_researcher import GPTResearcher

researcher = GPTResearcher(query="2025 年中国新能源汽车销量")
await researcher.conduct_research()

print(researcher.evidence_artifact_paths)
# {'json': 'outputs/research_xxx.evidence.json', 'md': 'outputs/research_xxx.evidence.md'}
```

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `EVIDENCE_EXTRACTION_ENABLED` | `false` | 总开关；由**顶层研究者**在 `conduct_research` 末尾自动触发 |
| `EVIDENCE_LLM` | `fast` | 未知域名分级兜底与证据抽取所用 LLM：`fast` 或 `smart` |
| `EVIDENCE_MAX_SOURCES` | `100` | 单次研究处理的来源数上限（超出按抓取顺序截断） |
| `EVIDENCE_MAX_CHARS_PER_SOURCE` | `30000` | 每个来源正文截断长度（字符） |
| `EVIDENCE_CHUNK_SIZE` | `8000` | 抽取分块大小（字符） |
| `EVIDENCE_CHUNK_OVERLAP` | `400` | 分块重叠（字符） |
| `EVIDENCE_CONCURRENCY` | `4` | 抽取并发的 LLM 调用数上限 |
| `RELIABILITY_RULES_PATH` | 空 | 自定义**规则表** YAML 路径；为空时使用随包发布的默认规则表 |
| `TIER_CACHE_PATH` | `data/tier_cache.json` | LLM 兜底判定的**等级缓存**（候选规则池） |

行为边界：

- 证据层由**顶层研究者**触发；**子研究者**（deep 子查询、detailed 子主题）显式抑制，不会重复抽取。
- 只有正文 ≥ 300 字符的来源会被抽取；无正文的来源仅保留**来源档案**（`scraped: false`）。
- 每条证据必须携带能在来源正文中逐字定位的**原文引用**；校验失败先让 LLM 纠正一次，仍失败则丢弃并记入 `rejected[]`（fail-closed，不保留未验证条目）。
- 抽取阶段的 LLM 花费计入现有成本统计，并单独归入 `evidence` 步骤（`researcher.get_step_costs()["evidence"]`）。

## 产物说明

一次研究会生成两个文件（`outputs/` 目录）：

| 文件 | 内容 |
| --- | --- |
| `<research_id>.evidence.json` | 全量结构化产物，含排障用 `rejected[]` |
| `<research_id>.evidence.md` | 可读**证据表**（概览、来源档案、证据表、丢弃记录） |

JSON 顶层结构（`schema_version` 用于 schema 演进后旧产物仍可解析）：

```json
{
  "schema_version": 1,
  "research_id": "research_xxx",
  "query": "2025 年中国新能源汽车销量",
  "generated_at": "2026-09-29T13:10:00+00:00",
  "summary": {"sources": 10, "sources_scraped": 10, "evidence": 20, "rejected": 11,
               "by_tier": {"A": 1, "B": 2, "C": 5, "D": 2}},
  "sources": [{
    "id": "S-001", "url": "https://stats.gov.cn/...", "domain": "stats.gov.cn",
    "title": "统计公报", "publisher": "国家统计局", "tier": "A",
    "org_type": "government", "assigned_by": "rule",
    "matched_rule": "domain_exact:stats.gov.cn", "scraped": true
  }],
  "evidence": [{
    "id": "E-001", "source_id": "S-001", "entity": "新能源汽车", "metric": "销量",
    "value_type": "number", "value": 12888000, "value_raw": "1288.8万辆",
    "unit": "辆", "period": {"type": "year", "start": "2024", "end": "2024", "raw": "2024年"},
    "region": "全国", "scope": "全年累计", "quote": "……原文摘录……",
    "extracted_at": "...", "extractor": "openai:gpt-x"
  }],
  "rejected": [{"source_id": "S-001", "reason": "quote_not_found", "detail": "..."}]
}
```

要点：

- `value_type` 支持 `number` / `range` / `ratio` / `text`；`value` 是归一化值（如 `1288.8万辆` → `12888000`），`value_raw` 保留原文写法。
- `period` 记录统计期间（`type/start/end/raw`）；`scope` 是**口径**（统计定义），口径不同的数值不合并。
- `sources[].assigned_by` 标记判定方式：`rule`（规则表命中）/ `llm`（LLM 兜底，最高 C）/ `default`（无法判定，D）。
- `rejected[].reason`：`quote_not_found`（引用无法定位，或纠正后仍失败）或 `parse_failed`（LLM 输出无法解析成 JSON）。
- 同一来源内相同数据（指标+数值+期间）去重；跨来源重复保留（合并属后续阶段）。

### WebSocket 事件

开启后，研究过程中会推送 `type: "evidence"` 事件：

```json
{"type": "evidence", "content": "started",  "output": "🧾 Evidence layer started for 10 sources", "metadata": {"status": "started", "sources": 10}}
{"type": "evidence", "content": "completed", "output": "🧾 Evidence layer completed: 20 evidence / 11 rejected from 10 sources",
 "metadata": {"status": "completed", "sources": 10, "sources_scraped": 10, "evidence": 20, "rejected": 11,
               "by_tier": {"A": 1, "B": 2, "C": 5, "D": 2}}}
```

研究结束的 `path` 事件在开启时会额外带 `evidence` 键（JSON + Markdown 下载路径，正斜杠 + URL 编码，格式与报告文件一致）：

```json
{"type": "path", "output": {"pdf": "outputs/...", "docx": "outputs/...", "md": "outputs/...",
                             "json": "outputs/...json",
                             "evidence": {"json": "outputs/research_xxx.evidence.json",
                                           "md": "outputs/research_xxx.evidence.md"}}}
```

## 规则表维护

**规则表**是随包发布的 YAML 数据文件（默认 `gpt_researcher/evidence/rules/default_rules.yaml`），业务可复制一份自行维护并设置 `RELIABILITY_RULES_PATH` 指向它，不需要改代码发版。

```yaml
version: 1
rules:
  - match: domain_exact      # 精确域名：host 完全相等
    value: stats.gov.cn
    tier: A
    publisher: 国家统计局
    org_type: government
  - match: suffix            # 后缀：host 等于或以其结尾（子域命中）
    value: .gov.cn
    tier: A
    org_type: government
```

- 判定优先级：`domain_exact` > `suffix`（**最长后缀胜**）> LLM 兜底 > 默认 D。域名匹配前统一规范化（去 scheme/路径/端口，取 host）。
- `tier` 取 `A/B/C/D`；`publisher`、`org_type` 可省略。`org_type` 建议取：`government | international_org | association | institute | brokerage | consulting | academia | media | central_media | portal | ugc`。
- 每次调研发出的**来源档案**会记录 `matched_rule`（如 `domain_exact:stats.gov.cn`、`suffix:.gov.cn`），等级可复现、可解释。

## 等级缓存与提拔流程

未知域名由 LLM 兜底判定（最高 C），结果写入**等级缓存**（`TIER_CACHE_PATH`，默认 `data/tier_cache.json`）：同一域名第二次出现直接命中缓存，不重复消耗调用。

```json
{"version": 1, "domains": {"some-site.example": {"tier": "C", "publisher": "某行业网站", "org_type": "media", "assigned_at": "..."}}}
```

建议的提拔流程（定期进行，缓存即候选规则池）：

1. 导出缓存：查看 `data/tier_cache.json`，按域名/等级/publisher 人工审阅（重点审查 `tier: C` 的常用站点）。
2. 确认后的条目并入规则表 YAML（`domain_exact` 或 `suffix`，补 `publisher`/`org_type`），重新设置 `RELIABILITY_RULES_PATH` 或直接提交默认表变更。
3. 对应域名可从缓存中删除（规则表优先级更高，留着也不影响判定，但会干扰下次审阅）。
4. 规则表变更后无需改代码；容器部署时注意将自定义规则表与缓存挂载为持久化卷。

## 黄金用例验收

用真实查询跑通端到端流程并抽查引用命中率、等级判定与成本归属：

```bash
python scripts/evidence_golden_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
python scripts/evidence_golden_acceptance.py "新能源补贴政策" --domains stats.gov.cn,miit.gov.cn
```

脚本会输出 summary、20 条抽样证据的原文引用逐字命中率（要求 100%）、来源等级与规则表一致性、产物路径与 `evidence` 步骤成本，并在全部通过时以 `PASS` 退出。
