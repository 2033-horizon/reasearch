# 市场洞察 Agent（GPT Researcher 证据层）

在开源项目 [GPT Researcher](https://github.com/assafelovic/gpt-researcher) 之上的二次开发分支：为行业/企业调研增加一层**证据层（Evidence Layer）**，把抓取到的网页素材变成**可溯源、可分级、可交叉验证**的结构化证据，再由证据驱动报告写作。

- 术语表：[`CONTEXT.md`](CONTEXT.md)
- 设计决策：[`docs/adr/`](docs/adr/)（ADR-0001 ~ 0005）
- 阶段规格：[`.scratch/market-agent/spec.md`](.scratch/market-agent/spec.md)（阶段一）、[`spec-phase2.md`](.scratch/market-agent/spec-phase2.md)（阶段二）
- 使用与配置详解：[`docs/docs/gpt-researcher/gptr/evidence-layer.md`](docs/docs/gpt-researcher/gptr/evidence-layer.md)
- 交接说明：[`HANDOVER.md`](HANDOVER.md)

## 解决什么问题

1. 行业/企业信息人工搜集耗时长；
2. 来源混杂，报告数据没有逐条溯源，无法确认是否编造；
3. 人工核实要回原网页逐字比对，低可信来源还要多方交叉核对，最费时间。

## 核心能力

- **来源分级**：按发布主体把每个来源评为 A（政府及政府间组织）/ B（权威机构与央媒）/ C（行业与正规媒体）/ D（自媒体及其他）。规则表（YAML 数据文件）命中优先；未知域名 LLM 兜底且最高只能判 C，无法判定默认 D；判定结果缓存，可人工提拔为规则。
- **证据抽取（fail-closed）**：从正文抽取证据条目（指标、数值、口径、原文引用），引用必须能在原文中**逐字定位**，否则丢弃并记录原因——不保留未验证条目，防编造。
- **聚类与裁决**：同一指标（实体、指标、期间、地域、单位相同）且口径一致的跨来源条目归并为**证据组**；按裁决策略表判定：A/B 单条采纳（B 可配），C/D 需 2/3 条**独立来源**（按发布主体去重，转载只计一条）；数值容差 ±5% 内取代表值，超出容差按等级→时效裁定，无法裁定进待审。
- **例外复核**：复核页面只列出待审证据组（冲突/证据不足），并排展示各来源的等级/主体/数值/口径/引用/URL，支持采纳 / 否决 / 指定主来源；记录复核人与时间。
- **证据驱动报告**：正文只使用有效结论（采纳 / 交叉验证通过）；数据点带 `[^n]` 上标；**Word 报告注入真脚注**（标题 — 发布主体（等级）· 日期 · URL，URL 可点击，不依赖 pandoc）；待审数据默认进报告附录；复核后可**只重跑写作**生成新版本报告，旧版保留。
- **留存与审计**：来源、证据、证据组、裁决、复核与运行历史全部落单文件 SQLite；JSON + Markdown 证据产物（快照）可下载。
- **实时进度**：WebSocket 推送证据抽取、聚类、裁决进度事件；研究结束的 `path` 事件附带证据产物下载路径、报告版本与复核入口。

## 快速开始

环境要求：Python 3.11+（开发环境为 Windows + Python 3.12）。

```bash
# 1. 安装依赖
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置
# Windows:  copy .env.example .env
# macOS/Linux:  cp .env.example .env
# 然后填写三组必填项（大模型 / 搜索 / Embedding），详见 .env.example 内注释

# 3. 启动（Windows 可直接双击 start.bat）
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，输入行业或企业名称开始调研。研究结束后：

- 报告文件在 `outputs/`（Markdown / DOCX / PDF）；
- 证据产物为 `outputs/<research_id>.evidence.json` 与 `.evidence.md`；
- 存在待审证据组时，`path` 事件给出复核入口 `/review/<research_id>`。

## 配置

所有运行时配置都在根目录 `.env`（模板见 [`.env.example`](.env.example)）。关键项：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` | - | LLM 三档。接 OpenAI 兼容网关时使用 `openai:模型名` + `OPENAI_BASE_URL` |
| `RETRIEVER` | `tavily` | 本项目推荐 `bocha`（国内中文搜索） |
| `EMBEDDING` | `openai:...` | 本项目默认 `dashscope:text-embedding-v4` |
| `EVIDENCE_EXTRACTION_ENABLED` | `false` | 证据层总开关（阶段一） |
| `ADJUDICATION_ENABLED` | `false` | 聚类/裁决/证据驱动写作开关（阶段二） |
| `EVIDENCE_CONCURRENCY` | `4` | 证据抽取并发；网关限流时应调低 |

完整配置项及各参数含义、行为边界见 [`docs/docs/gpt-researcher/gptr/evidence-layer.md`](docs/docs/gpt-researcher/gptr/evidence-layer.md)。

### 数据文件（业务可维护，不必改代码）

| 文件 | 作用 | 覆盖方式 |
| --- | --- | --- |
| `gpt_researcher/evidence/rules/default_rules.yaml` | 来源分级规则表（域名 → A/B/C/D） | 复制后设 `RELIABILITY_RULES_PATH` 指向副本 |
| `gpt_researcher/evidence/rules/adjudication_rules.yaml` | 裁决策略表（各等级独立来源门槛、数值容差、裁定顺序） | 复制后设 `ADJUDICATION_RULES_PATH` 指向副本 |
| `data/tier_cache.json` | LLM 兜底评级缓存（候选规则池，可定期审阅提拔） | `TIER_CACHE_PATH` |
| `data/evidence.db` | SQLite 证据库（运行/来源/证据/裁决/复核） | `EVIDENCE_DB_PATH`，置空则不落库 |

## 复核页面与接口

调研结束的 `path` 事件给出复核入口（`/review/<research_id>`），页面只展示待审证据组，可执行采纳 / 否决 / 指定主来源，并填写复核人。对应接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/research/{research_id}/evidence` | 摘要、版本记录、证据组、待审组明细与引用映射 |
| `POST /api/research/{research_id}/reviews` | `{group_id, action: accept\|reject\|set_representative, representative_source_id?, reviewer?}` |
| `POST /api/research/{research_id}/regenerate` | 只重跑写作阶段，产出 `v{n}` 新报告并刷新证据快照 |

复核与后端按内网工具设计，**无登录鉴权**，请勿直接暴露公网。

## 测试与验收

```bash
# 离线测试套件（不需要网络/密钥）
python -m pytest tests/evidence tests/backend tests/test_evidence_integration.py -q

# 全量套件
python -m pytest -q
```

真实端到端验收脚本（使用 `.env` 中配置的检索器与 LLM，会产生 API 费用）：

```bash
# 阶段一：来源分级 + 证据表
python scripts/evidence_golden_acceptance.py "2024年中国新能源汽车销量" --max-sources 10

# 阶段二：聚类/裁决/证据驱动报告/复核重生成
python scripts/evidence_phase2_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
```

阶段二脚本全部通过时以 `PASS` 退出。Word/WPS 脚注呈现与"另存 PDF 保留脚注"需人工确认。

## 项目结构（二次开发部分）

```
gpt_researcher/evidence/          证据层核心
  layer.py                        入口：触发时机、并发编排、进度事件
  tiering.py                      来源分级引擎 + 等级缓存
  extraction.py                   分块抽取 + 原文引用逐字校验
  adjudication.py                 聚类、裁决（独立性/转载/容差/代表值/待审）
  store.py                        SQLite 存储（user_version 轻量迁移）
  writing.py                      证据驱动写作（有效结论、上标、附录）
  docx.py                         DOCX 真脚注注入（自研 OOXML）
  models.py / parsing.py          数据模型与 JSON 容错解析
  rules/*.yaml                    分级规则表、裁决策略表（数据文件）
backend/server/review.py          复核页面、复核/重生成 JSON 接口
frontend/scripts.js               进度事件展示
scripts/evidence_*_acceptance.py  黄金用例验收脚本
.scratch/market-agent/            规格与实施工单
docs/adr/                         架构决策记录
```

## 已知限制

- 证据层由顶层研究者触发；DetailedReport 的子主题来源暂未聚合（待上游 deep/detailed 流程合并后统一）。
- PDF 直出（不经 Word）无脚注；脚注仅在 Word/DOCX 路径。
- 脚注"日期"当前取抓取日期，来源发布日期尚未采集。
- 前后端无鉴权；跨调研的已裁决结论复用、多用户权限、国际来源规则表等尚未实现。
- 部分中文站点抓取可能超时或被反爬；索引/表格类内容召回有损失。

## 开源与致谢

本项目基于 [GPT Researcher](https://github.com/assafelovic/gpt-researcher)（作者 Assaf Elovic 及社区）二次开发，沿用其开源许可（见 [`LICENSE`](LICENSE)，Apache-2.0）。上游的完整文档、多语言 README 与社区入口见上游仓库。
