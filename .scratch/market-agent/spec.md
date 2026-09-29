# 证据层（阶段一）：可溯源证据采集与来源可靠度分级

Status: ready-for-agent

> 后续阶段见 `spec-phase2.md`（聚类→裁决→复核→证据驱动报告→SQLite）。

## Problem Statement

领导需要定期做行业/企业调研并输出洞察，但目前在 GPT Researcher 基础上生成的报告存在三个痛点：

1. **搜集耗时**：想调查的行业/企业信息分散在各处，人工搜集要花数小时到数天
2. **真伪难辨**：来源混杂政府、机构、媒体、个人发布，可靠性参差；报告中的数据没有逐条溯源，无法确认是否编造
3. **核实与整理耗时**：人工核实一条数据要回原网页逐字比对，低可信来源还需要多方交叉核对——这个环节领导目前纯手工，最费时间

现有 GPT Researcher 的上下文压缩流程会丢失"数据 ↔ 来源"的绑定，也没有对来源权威度的分级，无法支撑"数据可溯源、来源可评级"的工作方式。

## Solution

在现有研究流程之后插入**证据层**：每次调研自动把抓取到的每个**来源**转换成结构化**证据条目**（指标、数值、口径、原文引用、来源标识），并给每个来源按**发布主体**评定**来源等级**（A 政府 / B 权威机构与央媒 / C 行业与正规媒体 / D 自媒体）。

关键行为：

- 只有**原文引用**能在来源正文中逐字定位的数据点才进入证据表（fail-closed，防编造）
- A/B 等级由可维护的**规则表**判定；未知域名由 LLM 兜底且最高只能判 C，无法判定默认 D
- 产出可下载的**证据产物**（JSON + Markdown 证据表），领导可直接抽查；每个来源档案记录判定依据

本阶段只交付"可信证据表"，不改变报告写作；后续阶段（交叉验证、证据驱动报告）在此基础上构建。

## User Stories

1. 作为领导，我想要输入一个行业或企业名称就获得尽可能全面的信息，这样我不必自己花几小时到几天搜集
2. 作为领导，我想要报告与证据表中的每个数据都能回溯到原始网页，这样我可以随时核实而不是信任黑盒结论
3. 作为领导，我想要来源按权威度分为 A/B/C/D 四级，这样我能一眼判断数据可信度，而不是面对一堆无差别链接
4. 作为领导，我想要够权威的来源（A 级）数据直接被采纳，这样高可信数据不用我重复核实
5. 作为领导，我想要低等级来源（C/D）的数据被明确标记，这样我知道哪些需要谨慎对待或交叉验证
6. 作为领导，我想要看到哪些数据因无法逐字溯源而被系统丢弃，这样我对"不编造"有直接信心
7. 作为领导，我想要证据概览按等级统计（各级多少条），这样我能快速评估本次调研的证据质量
8. 作为领导，我想要证据表可以下载（JSON 和 Markdown），这样我能在会上直接引用或转给同事
9. 作为领导，我想要每条证据带统计口径，这样我不会把"零售额"和"出货额"这类不同口径的数字当成同一个事实
10. 作为领导，我想要本次调研的 LLM 花费被记录（含证据抽取阶段），这样部门预算可核算
11. 作为市场分析师，我想要系统能对指定权威域名做定向搜索，这样政府/机构数据源能被优先覆盖
12. 作为市场分析师，我想要"尽可能多"的搜索收敛在明确上限内（来源数、正文长度、并发、模型），这样每次调研成本可预期
13. 作为市场分析师，我想要调研过程中实时看到证据层进度（已扫描来源、已抽证据、各级数量），这样我知道任务进行到哪一步
14. 作为市场分析师，我想要同一页面上重复出现的相同数据只保留一条，这样证据表不冗余
15. 作为市场分析师，我想要数值同时保存原文写法和归一化值（"1.6亿" → 160000000），这样后续比对计算没有歧义
16. 作为市场分析师，我想要每条证据标明所属主体（企业/行业/地区），这样"比亚迪的销量"和"行业销量"不会混淆
17. 作为市场分析师，我想要区间值（"10-15%"）和文本型事实（"X 收购 Y"）也能记录，而不是只支持单一数字
18. 作为市场分析师，我想要系统遇到未知域名时保守分级（默认 D，LLM 最多判 C），这样不可信来源不会被误抬为权威
19. 作为开发者，我想要来源分级规则以数据文件维护而不是写死在代码里，这样调整白名单不需要改代码发版
20. 作为开发者，我想要规则判定优先级明确（精确域名 > 后缀 > LLM 兜底 > 默认 D），这样任何来源的等级可复现、可解释
21. 作为开发者，我想要 LLM 对未知域名的判定结果被缓存，这样同一域名不重复消耗调用，且缓存可作为候选规则池人工提拔
22. 作为开发者，我想要证据层由配置开关控制且默认关闭，这样未启用时现有报告行为零变化
23. 作为开发者，我想要证据抽取在子研究者上被抑制，这样 deep/detailed 流程不会重复抽取、证据产物不碎片化
24. 作为开发者，我想要查询里的 site: 语法被正确转换为博查 API 的 include 参数，这样定向搜索行为与其他检索器一致
25. 作为开发者，我想要规则表文件随包发布（含打包声明），这样部署到容器/服务器不会丢文件
26. 作为事后复核者，我想要每条被丢弃的证据带原因（引用无法定位/解析失败）记录在案，这样我能判断是抽取 prompt 问题还是来源问题
27. 作为事后复核者，我想要证据产物带 schema_version，这样 schema 演进后旧产物仍可解析
28. 作为事后复核者，我想要来源档案记录判定方式（规则命中还是 LLM 兜底），这样每个等级都有判定依据可查

## Implementation Decisions

### 模块与接口

- 新建 **evidence 模块**：按 ADR-0001 独立于编排，接口为"聚合后的来源列表 → 证据产物"（内存结果 + 落盘产物）。内含：`SourceProfile` / `EvidenceItem` 模型、分级引擎、抽取器、产物序列化。LLM、规则表路径、输出去向通过参数注入，便于测试
- 新建 **分级规则体系**：规则表数据文件（YAML）+ LLM 兜底 + 等级缓存，按 ADR-0002
- **检索器修改**：博查检索器补齐域名定向；其余检索器不动
- **agent 装配**：顶层研究者 `conduct_research` 末尾自动触发证据层；新增构造参数 `is_sub_researcher`（默认 False），deep/detailed 两处创建子研究者时传 True
- **配置体系**：新增配置项（含类型声明与默认值）：`EVIDENCE_EXTRACTION_ENABLED`(false)、`EVIDENCE_LLM`(fast)、`EVIDENCE_MAX_SOURCES`(100)、`EVIDENCE_MAX_CHARS_PER_SOURCE`(30000)、`EVIDENCE_CHUNK_SIZE`(8000)、`EVIDENCE_CHUNK_OVERLAP`(400)、`EVIDENCE_CONCURRENCY`(4)、`RELIABILITY_RULES_PATH`(空→包内默认规则表)、`TIER_CACHE_PATH`(data/tier_cache.json)
- **产物接入**：证据产物写入 outputs 目录，命名 `<research_id>.evidence.json` / `.evidence.md`；新 WebSocket 事件类型 `evidence`（进度与统计）；`path` 事件增加 `evidence` 键；路径格式对齐报告文件惯例（URL quote + 正斜杠）
- **打包**：在打包声明中显式 include 规则表数据文件

### 数据模型（设计中定稿的 schema，直接作为契约）

证据产物 JSON 顶层：

```json
{
  "schema_version": 1,
  "research_id": "...",
  "query": "...",
  "generated_at": "...",
  "summary": {"sources": 0, "sources_scraped": 0, "evidence": 0, "rejected": 0,
               "by_tier": {"A": 0, "B": 0, "C": 0, "D": 0}},
  "sources": [],
  "evidence": [],
  "rejected": [{"source_id": "S-001", "reason": "quote_not_found", "detail": "..."}]
}
```

`SourceProfile`：`id`(S-001 起) / `url` / `domain` / `title?` / `publisher?` / `tier`(A-D) / `org_type?`(government | international_org | association | institute | brokerage | consulting | academia | media | central_media | portal | ugc) / `assigned_by`(rule | llm | default) / `matched_rule?` / `scraped`(是否有正文)

`EvidenceItem`：`id`(E-001 起) / `source_id` / `entity?`(指标所属主体) / `metric` / `value_type`(number | range | ratio | text) / `value`(归一化值) / `value_raw`(原文写法) / `unit?` / `period{type, start?, end?, raw}` / `region?` / `scope?`(口径) / `quote` / `extracted_at` / `extractor`(模型标识)

规则表 YAML：

```yaml
version: 1
rules:
  - match: domain_exact
    value: stats.gov.cn
    tier: A
    publisher: 国家统计局
    org_type: government
  - match: suffix
    value: .gov.cn
    tier: A
    org_type: government
```

### 分级行为

- 优先级：`domain_exact` > `suffix`（最长后缀优先）> LLM 兜底 > 默认 D
- LLM 兜底最高只能判 C；A/B 必须规则命中；LLM 判定结果标记 `assigned_by=llm` 并持久化到等级缓存（domain → tier/org_type/判定时间），作为候选规则池
- 域名匹配前规范化：去 scheme/路径/端口，取 host

### 抽取行为

- 输入：聚合后的来源列表；仅处理有正文且正文 ≥300 字符的**来源**；无正文条目只进 `sources`（`scraped=false`）
- 分块：每来源正文截断至 30k 字符，按 8k 块 / 400 重叠切分；并发 4；块级结果合并
- 抽取模型默认 FAST_LLM（`EVIDENCE_LLM` 可切 fast/smart）；成本计入现有成本统计
- 同源内按 `(source_id, metric, value, period)` 去重；跨源重复保留（合并属后续阶段）
- JSON 解析沿用仓库既有的容错模式（json_repair + 多级提取），解析失败记入 `rejected[]`

### 原文引用校验（ADR-0003）

- 引用与正文双方 NFKC 归一化 + 空白折叠后做严格子串匹配
- 失败时让 LLM 按原文纠正一次，仍失败则丢弃该证据条目并记入 `rejected[]`
- 不做模糊匹配；不保留未验证条目

### 博查域名定向

- `query_domains` 规范化为 host 列表后以 `include`（`|` 分隔，≤100 个）传给博查 API；规范化后为空则不传该字段
- 从查询中提取 `site:` 记号并入 `include`，并从查询文本中剥离（与 Tavily 行为一致）

### 引用 ADR

- ADR-0001 证据层独立于编排：顶层触发、子研究者抑制
- ADR-0002 来源等级规则即数据：LLM 兜底限 C、未知默认 D
- ADR-0003 原文引用 fail-closed：不能逐字定位即丢弃

## Testing Decisions

**好测试的标准**：只测外部行为，不测实现细节。以 evidence 模块公开入口、GPTResearcher、博查检索器三个 seam 的输入输出为断言对象；不断言内部函数调用顺序、不硬编码 prompt 全文（prompt 中的 schema 契约可用结构性断言）。测试默认离线（沿用仓库 `GPTR_BLOCK_NETWORK` 网络熔断约定），LLM 用假实现注入。

**被测模块与用例**（seam 按确认结果：1 新增 + 2 复用）：

- **evidence 模块入口（新增 seam）**：
  - 分级：精确域名/后缀匹配/子域/优先级（精确 > 后缀，最长后缀胜）、协议端口归一化、LLM 兜底限 C、未知默认 D、`assigned_by` 标记
  - 抽取：脏 JSON（fenced/带散文/字段缺失）容错、跳过无正文与过短来源、同源去重
  - 引用校验：空白/换行/全半角/NFKC 变体通过；编造引用被拒并进 `rejected[]`；纠正重试路径
  - 产物：JSON 结构与 `schema_version`、summary 计数、Markdown 证据表可生成
- **GPTResearcher（复用，现有最高入口）**：
  - 回归：开关关闭 → 无产物、无行为变化
  - 装配：开关开启 → 顶层研究者产出产物且路径可达；子研究者被抑制；deep 路径来源聚合后仍触发
- **博查检索器（复用 BoChaSearch.search）**：mock HTTP，验证 `include` 参数、`site:` 解析并剥离、域名规范化、空域名不传参

**既有同类测试作为先例**：`tests/test_deep_research_parsing.py`（monkeypatch LLM + 参数化脏输出）、`tests/test_source_curator_json_parsing.py`（AsyncMock + 伪造 researcher）、`tests/retrievers/`（mock HTTP）、`tests/conftest.py`（网络熔断）。

**验收（黄金用例）**：真实查询跑一次完整流程，抽查 20 条证据的引用逐字命中率 100%，来源等级与规则表一致。

## Out of Scope

- 交叉验证与证据裁决（"高可信 1 条采纳、低可信多方验证"）——后续阶段
- 报告写作接入（报告引用证据、证据附录、写作只用已验证证据）
- 证据持久化数据库（SQLite）与人工复核界面
- 表格转 Markdown 的抓取层改造（本阶段接受表格类内容召回损失）
- Excel/CSV 导出；前端证据面板与下载按钮（本阶段只做 WS 事件与产物文件）
- DetailedReport 子主题来源的完整覆盖（待 deep/detailed 合并后由合并编排器统一）
- 其他检索器的域名定向（duckduckgo/bing/brave/searx 等仍是 TODO）；`exclude` 域名过滤
- 非中文场景的规则表与多语言抽取

## Further Notes

- 术语表见仓库根 `CONTEXT.md`（证据层/来源/发布主体/来源等级/来源档案/证据条目/口径/原文引用/规则表/子研究者/顶层研究者），实现与文档必须使用该词汇
- 三个决策已落档 ADR（见 `docs/adr/`），本 spec 与其冲突时以 ADR 为准
- 开发工作流遵循仓库 `AGENTS.md`：每个关键步骤本地提交（不推送），commit message 中文结构化
- 建议实施切片（链式依赖）：①文档基线 ②模型+产物 ③规则引擎+缓存 ④抽取+引用校验 ⑤阶段接入（触发/抑制/配置）⑥博查域名定向 ⑦WS 事件与后端接入 ⑧测试+黄金用例+README；⑥独立，其余链式
- 已知风险：表格内容召回损失（已记 backlog）；抽取阶段的 LLM 成本与耗时需在预算内观察；Windows 下路径格式需统一为 quote + 正斜杠
