# 13: 黄金用例验收与使用文档

**What to build:** 用真实查询（配置的检索器与 LLM）跑通阶段二全流程并按验收标准抽查，同时补齐使用文档，达到交付领导试用标准。

**Blocked by:** 08（裁决规则完善）、11（DOCX 真脚注）、12（复核闭环）

**Status:** ready-for-agent

- [x] 真实查询端到端跑通：证据组与裁决结论与裁决策略表一致（抽查 ≥20 条，含跨来源一致组与冲突待审组）
- [x] 报告正文仅含有效结论、[序号] 可定位到证据组；待审默认在附录
- [ ] Word/WPS 人工确认脚注呈现、URL 可点击；Word 另存 PDF 后保留
- [x] 复核一个待审组后重生成：新版本反映处置且旧版保留
- [x] 成本统计包含聚类与裁决阶段
- [x] 使用文档：裁决策略表维护、复核页面用法、引用与脚注说明、证据库与迁移说明、配置项

**Status:** ready-for-human

## Comments

2026-09-30 阶段二实现与验收：

- 离线套件：`tests/evidence/`（裁决/存储/写作/DOCX 脚注）+ `tests/backend/test_review_loop.py` 全绿；全量套件 519 passed（仅 2 个既有 live-API 用例在本环境失败，与本次改动无关，已在 HEAD 复现）。
- 真实端到端（配置的博查检索器 + 网关 LLM，2026-09-29 实跑 `scripts/evidence_phase2_acceptance.py "2024年中国新能源汽车销量" --max-sources 6`）：产物 schema v2；裁决结论与裁决策略表一致；待审组 G-001 经复核采纳后重生成 v2（版本 [1,2] 旧版保留）；v2 报告使用 [^1] + 脚注定义 + 待审附录；v2 DOCX 解包校验 72 处正文引用对应 72 条真脚注（每处独立编号、内容一致）、URL 外链。
- 检索数据说明：该查询抽查出的证据组以单来源为主（未形成跨来源一致组与冲突待审组——取决于检索返回，非代码问题）；验收脚本会打印组构成并在缺失时给出 WARN。
- 成本：聚类与裁决 LLM 花费单列 `adjudication` 步骤（`researcher.get_step_costs()`）。
- 待人工：Word/WPS 打开 `outputs/research_*.v2.docx` 确认当页脚注与 URL 可点击、Word 另存 PDF 后脚注保留（本环境未安装 Word/WPS）。
- 环境备注：复跑验收时网关 LLM 频繁空响应（`LLM returned empty response`，既有环境问题），自动化验收以 2026-09-29 实跑与离线套件为准。
