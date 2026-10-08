# 项目交接文档 — 市场洞察 Agent（GPT Researcher 证据层）

> 交接对象：接手本仓库的开发者/运维。
> 本文覆盖：项目目标、当前状态、已知问题、配置修改位置、运行与验收方式。
> 术语以仓库根 `CONTEXT.md` 为准；设计决策以 `docs/adr/` 为准。

---

## 1. 项目背景与目标

本仓库是开源项目 **GPT Researcher**（`assafelovic/gpt-researcher`）的二次开发分支，目标是把通用的"自动调研报告"改造成面向领导/市场分析师的**市场洞察 Agent**。

原始痛点（见 `.scratch/market-agent/spec.md`）：

1. 行业/企业信息人工搜集耗时数小时到数天；
2. 来源鱼龙混杂，报告数据无逐条溯源，无法确认是否编造；
3. 人工核实数据要回原网页逐字比对，低可信来源还要多方交叉核对，最费时间。

解决思路：在研究流程之后插入**证据层（Evidence Layer）**，分两阶段交付：

- **阶段一（已完成）**：抓取到的每个来源 → 结构化**证据条目**（指标、数值、口径、原文引用）+ **来源等级** A/B/C/D（按发布主体）。核心红线：原文引用必须能在正文中**逐字定位**，否则丢弃（fail-closed，防编造）。产出可下载的 JSON + Markdown 证据产物。
- **阶段二（已完成）**：在证据层之上做 聚类 → 规则化裁决 → 例外复核 → 证据驱动写作。报告正文只使用**有效结论**（采纳/交叉验证通过），每个数据点带 `[^n]` 上标，Word 显示**真脚注**（标题 — 发布主体（等级）· 日期 · URL，URL 可点击）；待审数据默认进附录；全部证据/裁决/复核落 SQLite；复核后可只重跑写作、生成新版本报告。

规格文件：`.scratch/market-agent/spec.md`（阶段一）、`spec-phase2.md`（阶段二）；术语表 `CONTEXT.md`；ADR 5 篇在 `docs/adr/`。

---

## 2. 当前状态

### 2.1 完成度

13 个实施工单（`.scratch/market-agent/issues/`）全部实现完毕：

| 工单 | 内容 | 状态 |
|---|---|---|
| 01–06（阶段一） | 来源分级、证据抽取、顶层接入、博查域名定向、后端流式、黄金用例 | done |
| 07–10（阶段二） | 聚类裁决基线、裁决规则完善、SQLite 证据库、证据驱动写作 | done |
| 11 | DOCX 真脚注注入 | **ready-for-human**（自动化验证通过，Word/WPS 人工确认未做） |
| 12 | 复核闭环与重生成 | done |
| 13 | 阶段二黄金用例验收与使用文档 | **ready-for-human**（见 3.2 遗留项） |

### 2.2 代码结构（二次开发部分）

- **核心模块 `gpt_researcher/evidence/`**
  - `layer.py` — 证据层入口：顶层研究者触发、子研究者抑制、并发编排、WS 事件
  - `tiering.py` — 来源分级引擎（规则表 > LLM 兜底限 C > 默认 D）+ 等级缓存
  - `extraction.py` — 分块抽取 + 原文引用逐字校验（NFKC + 空白折叠严格子串）
  - `adjudication.py` — 聚类、裁决（独立性/转载/容差/代表值/待审）
  - `store.py` — SQLite 存储（`PRAGMA user_version` 轻量迁移）
  - `writing.py` — 证据驱动写作（有效结论筛选、上标、附录）
  - `docx.py` — 自研 OOXML 脚注注入（不依赖 pandoc）
  - `rules/default_rules.yaml` — 来源分级规则表（数据文件）
  - `rules/adjudication_rules.yaml` — 裁决策略表（数据文件）
- **后端**：`backend/server/review.py`（复核页面 + JSON 接口 + 重生成）、`backend/server/server_utils.py`（`path` 事件追加证据路径/复核入口）
- **前端（轻量版）**：`frontend/scripts.js`（证据/裁决进度事件展示）
- **验收脚本**：`scripts/evidence_golden_acceptance.py`（阶段一）、`scripts/evidence_phase2_acceptance.py`（阶段二）
- **文档**：使用文档 `docs/docs/gpt-researcher/gptr/evidence-layer.md`（配置项、规则表维护、复核页面、脚注、证据库迁移、验收步骤全在里面）

### 2.3 运行方式

```
start.bat          # 激活 .venv 并启动 uvicorn main:app，地址 http://127.0.0.1:8000
```

- 环境：Windows + Python 3.12 虚拟环境 `.venv`（已装好依赖）。
- Web UI、报告下载、证据产物下载、复核页面都在 8000 端口。
- 阶段二验收（真实检索 + 真实 LLM，需配置好的 key）：

```bash
python scripts/evidence_phase2_acceptance.py "2024年中国新能源汽车销量" --max-sources 10
```

### 2.4 测试与实跑证据

- 离线套件：`tests/evidence/`（84 passed）、`tests/backend/test_review_loop.py`、`tests/test_evidence_integration.py`（17 passed）；全量套件 2026-09-30 实跑 **520 passed, 2 skipped**（日志 `outputs/full_suite.log`）。
- 阶段二真实端到端实跑记录：`outputs/phase2_acceptance_run.log`、产物样例 `outputs/research_a65d60aa85eb.evidence.json` / `.v2.docx`（解包校验 72 处正文引用对应 72 条真脚注）。工单 13 的评论里有详细验收结论。

### 2.5 Git 状态

- 分支 `main`，**领先上游 `origin/main` 26 个本地提交，从未推送**（按 `AGENTS.md` 约定只做本地提交）。
- 远程：`origin` = 上游 assafelovic/gpt-researcher；`github` = `https://github.com/2033-horizon/reasearch.git`（未见推送记录，接手后先确认这个仓库的用途与权限）。
- 工作区干净。`.env`、`data/`、`my-docs/` 均已在 `.gitignore` 中。

---

## 3. 已知问题（交接重点）

### 3.1 环境/依赖类（最影响日常使用）

1. **LLM 中转站不稳定**：`deepseek-v4.1-flash` 曾频繁超时/空响应（实测 120s 无响应），2026-09-30 已临时切到 `qwen3.8-27b`（见 `.env` 注释）。日志中大量 `LLM returned empty response (attempt n/10)` 都源于此，不是代码 bug。线路恢复可切回。
2. **中转站并发限制约 2**：因此 `EVIDENCE_CONCURRENCY=2`、`LLM_KWARGS={"request_timeout": 300}`，调大并发只会排队更久。
3. **推理模型 token 预算**：该线路的模型是推理型，"思考" token 计入输出上限，`FAST_TOKEN_LIMIT=16000` 是为此调大的；改小会出现证据抽取返回空文本。
4. **DashScope embedding 偶发 500**（`Receive batching backend response failed!`），有自动重试，属中转侧抖动。
5. **部分中文站点抓取超时**（如 `m.cnhuoche.com`），内容过短会被判抓取失败，属网络环境问题。

### 3.2 验收遗留（工单 11 / 13 未勾完的项）

1. **Word/WPS 人工确认未做**：需用 Word 或 WPS 打开 `outputs/*.docx`，确认当页底部脚注为"标题 — 发布主体（等级）· 日期 · URL"、URL 可点击、Word 另存 PDF 后脚注保留（开发机没装 Word）。
2. **黄金用例覆盖面**：实跑抽查中未出现"跨来源一致组"与"冲突待审组"（取决于检索返回内容，非代码问题）；验收脚本已将其列为硬性检查，需要更丰富的查询复跑。脚本在缺失时会打 WARN。

### 3.3 设计边界（明确不做/推迟，不是 bug）

- 仅顶端研究者触发证据层；**DetailedReport 子主题来源未聚合**，等上游 deep/detailed 流程合并后由合并编排器统一（ADR-0001 已记录后果）。
- **PDF 直出无脚注**（脚注只在 Word/DOCX 路径）；Windows 缺 GTK 库导致 PDF 另有问题，属阶段二范围外。
- 复核页面与整个 FastAPI 后端**无登录鉴权**（按内网工具设计）；不要直接暴露公网。
- 跨调研的"已裁决结论复用"、多用户/权限、国际来源规则表、`exclude` 域名等均在 spec 的 Out of Scope。
- 日期字段目前取**抓取日期**，来源发布日期尚未采集（使用文档已注明）。

### 3.4 其他

- 最后一次提交信息 `9.30` 未描述内容，实际改动是：`DEEP_RESEARCH_BREADTH` 3→2、`DEEP_RESEARCH_DEPTH` 2→1（控制单次调研成本）、证据抽取进度事件、以及测试同步——接手后改配置时可留意默认值已被下调。
- 仓库根 `ISSUE_BACKLOG.md` 与 `.triage/` 是**上游 GitHub issue 分诊**的历史资料，与证据层开发无关，可忽略。
- `my-docs/` 为空目录（本地文档模式的数据目录）。

### 3.5 安全（交接时必做）

`.env` 内有**真实可用的三组密钥**（中转站 OpenAI key、博查 key、DashScope key），且以明文保存在工作目录。交接前建议：轮换这些 key，用安全渠道传递新 key；不要提交 `.env`（现已 gitignore）。

---

## 4. 配置修改位置速查

| 要改什么 | 改哪里 |
|---|---|
| **所有运行时设置（LLM/搜索/embedding/密钥/证据层开关）** | 仓库根 **`.env`**（当前生效值）与 `.env.example`（模板） |
| 各配置项的**类型声明** | `gpt_researcher/config/variables/base.py`（第 53–68 行为证据层相关） |
| 各配置项的**默认值** | `gpt_researcher/config/variables/default.py`（第 60–76 行为证据层相关） |
| **来源分级规则表**（域名 → A/B/C/D） | `gpt_researcher/evidence/rules/default_rules.yaml`；或复制一份后用 `RELIABILITY_RULES_PATH` 指向自定义副本 |
| **裁决策略表**（各等级所需独立来源数、数值容差 ±5%、裁定顺序） | `gpt_researcher/evidence/rules/adjudication_rules.yaml`；或 `ADJUDICATION_RULES_PATH` 指向自定义副本 |
| 未知域名的 LLM 分级缓存（候选规则池） | `data/tier_cache.json`（`TIER_CACHE_PATH`） |
| 证据/裁决/复核 SQLite 库 | `data/evidence.db`（`EVIDENCE_DB_PATH`，置空则不落库） |
| 启动命令/端口 | `start.bat`（uvicorn 127.0.0.1:8000）；应用入口 `main.py`、`backend/server/app.py` |
| 复核页面与接口 | `backend/server/review.py`（`/review/{research_id}`、`/api/research/{research_id}/...`） |
| 证据层行为参数（开关/条数/字符上限/分块/并发） | `.env` 中 `EVIDENCE_*`、`ADJUDICATION_*`、`REPORT_PENDING_APPENDIX` 等，说明见 `docs/docs/gpt-researcher/gptr/evidence-layer.md` |
| 深调研广度/深度（成本敏感） | `.env` 或默认值 `DEEP_RESEARCH_BREADTH/DEPTH`（当前默认 2/1） |

开发流程约定（`AGENTS.md`）：每个关键步骤本地 commit、不推送，中文结构化 commit message；工单放 `.scratch/<feature>/issues/`。

---

## 5. 接手后建议动作清单

1. 轮换 `.env` 里三组 API key，确认中转站当前可用模型名（`<中转站>/v1/models`）。
2. `start.bat` 起服务，跑一次简单查询，确认证据产物与复核页面可用。
3. 用 Word/WPS 补做工单 11/13 的人工确认（脚注呈现、URL 可点击、另存 PDF 保留）。
4. 选更丰富的查询复跑 `scripts/evidence_phase2_acceptance.py`，补齐"跨来源一致组 + 冲突待审组"的黄金用例覆盖。
5. 确认 `github` 远程（2033-horizon/reasearch）是否需要推送；当前 26 个提交只存在本地。
6. 若要继续开发，先读：`CONTEXT.md` → `docs/adr/` → 两份 spec → `docs/docs/gpt-researcher/gptr/evidence-layer.md`。
