---
sidebar_position: 8
---

# 证据层（来源分级、裁决与证据驱动报告）

证据层（Evidence Layer）在研究完成时自动运行，把抓取到的每个**来源**转换成结构化**证据条目**（指标、数值、口径、**原文引用**、来源标识），并给每个来源按**发布主体**评定**来源等级**（A 政府 / B 权威机构与央媒 / C 行业与正规媒体 / D 自媒体及其他），产出可下载的**证据产物**（JSON + Markdown）。

阶段二在证据层之上增加四个环节（`ADJUDICATION_ENABLED=true` 启用）：

1. **聚类**：同一指标（实体、指标、期间、地域、单位相同）且口径一致的跨来源条目归并为**证据组**，口径不同一律拆组；
2. **裁决**：按**裁决策略表**判定每个证据组（采纳 / 交叉验证通过 / 冲突待审 / 证据不足待审）；
3. **复核**：只处理例外项的复核页面（采纳 / 否决 / 指定主来源），处置后可重生成报告；
4. **证据驱动报告**：正文只用有效结论，数据点带 `[^n]` 上标，Word 显示真脚注；证据全部落 SQLite。

设计决策见仓库 `docs/adr/`（ADR-0001 顶层触发、ADR-0002 规则即数据、ADR-0003 引用 fail-closed、ADR-0004 裁决策略、ADR-0005 SQLite 证据库），术语见根目录 `CONTEXT.md`。

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
| `ADJUDICATION_ENABLED` | `false` | 阶段二总开关：聚类 + 裁决 + 证据驱动写作 + SQLite |
| `ADJUDICATION_RULES_PATH` | 空 | 自定义**裁决策略表** YAML 路径；为空时使用随包发布的默认策略表 |
| `EVIDENCE_DB_PATH` | `data/evidence.db` | SQLite 证据库路径；置空则不落库 |
| `ADJUDICATION_LLM` | `fast` | 指标归并 / 口径判定 / 事实判定所用 LLM：`fast` 或 `smart` |
| `ADJUDICATION_PENDING_BLOCKS_REPORT` | `false` | 为 `true` 时存在待审组则暂停报告生成（保守门禁） |
| `REPORT_PENDING_APPENDIX` | `true` | 待审清单默认列入报告附录；关闭后不出现 |

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
- 同一来源内相同数据（指标+数值+期间）去重；跨来源重复由聚类归并。

### 裁决产物（schema v2）

`ADJUDICATION_ENABLED=true` 时产物为 `schema_version: 2`，新增 `groups[]` 与 `citations{}`，`summary` 增加组数/裁决统计/待审数：

```json
{
  "schema_version": 2,
  "summary": {"sources": 10, "evidence": 20, "groups": 12,
               "verdicts": {"accepted": 4, "cross_validated": 6,
                             "conflict_pending": 1, "insufficient_pending": 1},
               "pending": 2, "reviewed": 0,
               "effective": {"accepted": 4, "cross_validated": 6,
                              "conflict_pending": 1, "insufficient_pending": 1, "rejected": 0}},
  "groups": [{
    "id": "G-001",
    "key": {"entity": "某公司", "metric": "新能源汽车销量", "period": {"raw": "2024年"},
             "region": null, "unit": "辆"},
    "scope": "全年累计",
    "members": ["E-001", "E-002"], "sources": ["S-001", "S-002"],
    "independent_sources": 2,
    "verdict": {"status": "cross_validated", "rule": "tier_minimum:C=2",
                 "reason": "…", "required": 2, "best_tier": "C", "conflicts": []},
    "effective_status": "cross_validated",
    "representative": {"evidence_id": "E-001", "source_id": "S-001", "value_raw": "100万辆"},
    "merge_log": [{"type": "metric_merged", "metrics": ["销量", "销售量"], "by": "llm"},
                   {"type": "repost", "source_id": "S-002", "repost_of": "某媒体", "reason": "quote_similarity"}],
    "review": null
  }],
  "citations": {"1": {"group_id": "G-001", "evidence_id": "E-001", "source_id": "S-001",
                       "url": "https://…", "metric": "新能源汽车销量",
                       "footnote": "统计公报 — 国家统计局（A）· 2026-09-29 · https://…"}}
}
```

- **裁决结论**（`verdict.status`）：`accepted`（A/B 级单条达标）、`cross_validated`（C/D 级独立来源数达标且数值在容差内一致）、`conflict_pending`（数值/语义冲突且等级与时效无法裁定）、`insufficient_pending`（独立来源数不足）。
- **有效结论**（`effective_status`）：有**复核**记录时以复核为准（采纳/指定主来源 → `accepted`，否决 → `rejected`），否则等于裁决结论；报告筛选只看有效结论。
- `citations` 是 `[序号] ↔ 证据组/条目/来源 URL` 的映射，与报告正文的 `[^n]` 编号一致。
- 被否决/待审数据保留在库中不删除；`verdict` 记录命中规则与参与来源，`merge_log` 记录归并/转载/拆组依据，可独立复盘。

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

开启裁决后还会推送 `type: "adjudication"` 进度事件（`clustering` → `clustered` → `adjudicated`，后者含组数/待审数/各级统计），`path` 事件额外带 `report_version` 与 `review` 复核入口：

```json
{"type": "adjudication", "content": "adjudicated",
 "output": "⚖️ 裁决完成：12 个证据组，待审 2 个",
 "metadata": {"status": "adjudicated", "groups": 12, "pending": 2,
               "verdicts": {"accepted": 4, "cross_validated": 6,
                             "conflict_pending": 1, "insufficient_pending": 1}}}

{"type": "path", "output": {"pdf": "outputs/...", "docx": "outputs/...", "md": "outputs/...",
                             "json": "outputs/...json",
                             "evidence": {"json": "outputs/…evidence.json", "md": "outputs/…evidence.md"},
                             "report_version": 1, "review": "/review/research_xxx"}}
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

## 裁决策略表维护

**裁决策略表**是随包发布的 YAML 数据文件（默认 `gpt_researcher/evidence/rules/adjudication_rules.yaml`），业务复制一份自行维护并设置 `ADJUDICATION_RULES_PATH` 指向副本即可，不需要改代码发版：

```yaml
version: 1
tiers:
  A: { min_independent_sources: 1 }
  B: { min_independent_sources: 1 }   # 可配置为 2（提高 B 级佐证门槛）
  C: { min_independent_sources: 2 }
  D: { min_independent_sources: 3 }
value_tolerance:
  relative: 0.05        # 数值相对容差 ±5%
resolution_order: [tier, recency]
```

- **门槛**：各等级所需**独立来源**条数。独立来源按**发布主体**去重（同主体多域名只计一条）；**转载**（有明确来源标注或原文引用高度相似）归并为一条不计新增独立来源；相似度处于灰区（0.75–0.9）按存疑处理、不算独立（保守）。
- **容差**：组内数值相对差在容差内视为一致；超出容差时按 `resolution_order`（等级 → 时效）裁定，无法裁定（如两条同级矛盾）进入待审。
- **口径**：口径文本一致或双方均无口径才归入同组；经 LLM 判定为"不同"一律拆组，判定结果缓存。指标同义表述（如"销量/销售量"）经 LLM 判定后归并，判定依据写入证据组 `merge_log`。
- 文件缺失或非法时保守回退到随包默认策略表，并记录告警日志。

## 复核页面

调研结束的 `path` 事件给出 `review` 入口（如 `/review/research_xxx`），页面只列出**待审证据组**，每组并排展示全部来源的等级/发布主体/数值/口径/原文引用/URL，可执行：

- **采纳**：该组作为有效结论进入重生成报告的正文；
- **否决**：该组不进入正文（记录保留，不删除）；
- **指定主来源**：从组内来源中选择代表值来源，采纳并以该来源作为脚注。

所有动作记录**复核人**与**时间**（页面上的"复核人"输入框）；复核不修改原裁决结论，另存复核记录，仅在**本次调研**内生效。对应的 JSON 接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/research/{research_id}/evidence` | 摘要、版本记录、证据组、待审组明细（含来源对比）与 citation 映射 |
| `POST /api/research/{research_id}/reviews` | `{group_id, action: accept\|reject\|set_representative, representative_source_id?, reviewer?}` |
| `POST /api/research/{research_id}/regenerate` | 只重跑写作阶段（正文写作，与基础报告一致；不重新检索抓取，不含引言/结论），产出 `v{n}` 新报告文件与刷新后的证据快照 |

## 引用与脚注

- 报告正文的每个数据点使用 Markdown 脚注语法 `[^n]`（n 为 citation 序号），文末含 `[^n]: 标题 — 发布主体（等级）· 日期 · URL` 定义区；未定义的 `[^n]` 编号会被剔除（不留悬空引用），未使用的定义不输出。其中"日期"当前取证据抓取日期（来源发布日期尚未采集）。
- **Word 报告显示真脚注**：正文上标与当页底部脚注一一对应，URL 可点击；同一编号多处引用时每处显示独立编号、内容一致的脚注。实现为自研 OOXML 注入（不依赖 pandoc 等外部工具），转换测试离线校验 `word/footnotes.xml`、正文 `w:footnoteReference` 与超链接关系。
- 人工验收步骤（Word/WPS）：打开 `outputs/*.docx`，确认当页底部脚注文本为"标题 — 发布主体（等级）· 日期 · URL"、URL 可点击；用 Word 另存为 PDF 后脚注仍保留。PDF 直出（未经 Word）无脚注，属阶段二范围外。
- 待审数据默认列入报告附录"附录：待审数据（未采信）"（`REPORT_PENDING_APPENDIX=false` 关闭）；`ADJUDICATION_PENDING_BLOCKS_REPORT=true` 时存在待审组则暂停生成正文。有效结论为空时报告直接给出"证据不足"提示，不编造内容。

## 证据库与迁移

阶段二起，运行、来源档案、证据条目、证据组、裁决结论、复核记录与判定缓存写入单文件 SQLite（`EVIDENCE_DB_PATH`，默认 `data/evidence.db`）；JSON + Markdown 产物由库导出，下载路径与格式契约不变。既有报告存储 `reports.json` 不受影响。

- **schema 版本**：通过 `PRAGMA user_version` 管理（当前 `1`），迁移为追加式脚本；升级不会清空既有运行与复核历史。
- **多次运行**：同一研究 ID 重复运行追加新运行记录（版本递增），不覆盖历史；重生成只新增 `v{n}` 报告文件与版本记录，旧版文件保留。
- **抽取缓存**：同一 URL + 同一正文命中的证据复用已校验条目（引用仍逐字校验，fail-closed），不重复消耗抽取 LLM 调用；聚类/裁决的 LLM 判定同样落库缓存，跨调研复用。
- 部署到容器时把 `data/` 挂载为持久化卷即可保留证据库与缓存。

## 黄金用例验收

阶段一验收（来源分级与证据表）：

```bash
python scripts/evidence_golden_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
```

阶段二验收（聚类/裁决/证据驱动报告/复核重生成）：

```bash
python scripts/evidence_phase2_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
```

阶段二脚本会校验：产物 schema v2 与 citation 映射、裁决结论与裁决策略表一致、20 条抽样引用逐字命中、正文只引用有效结论且脚注定义齐全、待审附录、DOCX 脚注部件、聚类/裁决阶段成本、以及"复核一个待审组 → 重生成新版本且旧版保留"；全部通过时以 `PASS` 退出。脚本使用真实检索器与 LLM，请在配置好密钥的环境中运行。Word/WPS 脚注呈现与 Word 另存 PDF 需按上文步骤人工确认。
