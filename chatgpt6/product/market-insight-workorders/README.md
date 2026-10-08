# 市场洞察 Agent 执行工单总览

| 项目 | 内容 |
| --- | --- |
| 版本 / 日期 | v1.0 / 2026-10-08 |
| 需求依据 | [市场洞察 Agent PRD](../market-insight-agent-prd.md) |
| 代码基线 | `0957c301ed06c2a5857b834358c7227c739041d4`；执行时先检查实际工作目录的新变化 |
| 工单数量 | 29 张：P0 首期 24 张、P1 后续 4 张、P2 规模化 1 张 |
| 当前状态 | 全部待执行；此交付仅完成拆单，未实施或验证功能 |
| 统一约定 | [COMMON.md](./COMMON.md)，每个执行 agent 都必须读取 |
| 机器可读清单 | [manifest.json](./manifest.json)，包含硬依赖、需求/用例映射、文件范围与集成责任 |

## 1. 如何派给其他 agent

先派 [MI-001](./MI-001-baseline-fixtures.md) 和 [MI-002](./MI-002-contracts.md)，二者可以并行。MI-002 的接口合入可用后，派 MI-003，再依据硬依赖继续。建议最初只保持 2–3 个不共享写入文件的 agent 并行；实际数量由负责人和运行环境决定。

每张工单都是一个可评审交付单元，不要求一个对话回合完成。工单末尾有可直接复制的执行指令；也可使用以下模板：

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-002-contracts.md。
先阅读 docs/product/market-insight-workorders/COMMON.md 和工单指定的 PRD 段落。
检查前置工单产物已合入当前工作目录，按本单范围实现、验证并完成交接。
保留其他人的改动，复用已冻结契约，不另建重复模型或绕过核验的实现。
结束时报告完成项、实际验证结果、变更范围、接口影响与未完成依赖。
```

将示例中的工单路径替换为目标工单即可。若给 agent 独立工作树，应把前置工单的可用提交包含进去；“另一个 agent 说已完成”不等于当前目录已具备依赖。

本套工单的前置关系指 **完成实现、通过本单检查且合入执行分支**。可在依赖尚未完成时依据冻结契约准备界面或模拟数据，但不得将未与真实接口联调的功能标为最终完成。

## 2. 执行顺序与并行批次

下表是根据硬依赖生成的拓扑分层，**不是周数或日历排期**。同一行可并行，前提是遵循 COMMON 中的共享文件责任；后续单只要自身依赖就绪即可开始，无需等待不相关的整批任务。

| 批次 | 硬依赖就绪后可执行 |
| --- | --- |
| W0 | [MI-001](./MI-001-baseline-fixtures.md)、[MI-002](./MI-002-contracts.md) |
| W1 | [MI-003](./MI-003-storage-versions.md) |
| W2 | [MI-004](./MI-004-task-api-auth.md)、[MI-005](./MI-005-runtime-budget.md)、[MI-008](./MI-008-source-policy.md) |
| W3 | [MI-006](./MI-006-html-pdf-evidence.md) |
| W4 | [MI-007](./MI-007-claim-extraction.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-011](./MI-011-tables-attachments.md) |
| W5 | [MI-009](./MI-009-single-source-milestone.md)、[MI-012](./MI-012-origin-independence.md) |
| W6 | [MI-013](./MI-013-cross-verification.md)、[MI-019](./MI-019-task-ui.md) |
| W7 | [MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-016](./MI-016-analysis-charts.md) |
| W8 | [MI-017](./MI-017-controlled-report.md)、[MI-020](./MI-020-evidence-review-ui.md) |
| W9 | [MI-018](./MI-018-report-validation-publish.md) |
| W10 | [MI-021](./MI-021-report-ui-chat.md)、[MI-022](./MI-022-exports.md) |
| W11 | [MI-023](./MI-023-integration-operations.md) |
| W12 | [MI-024](./MI-024-business-acceptance.md) |

P1/P2 默认在 P0 的业务验收 MI-024 完成后再派发。若业务明确调整范围，应同步 PRD、工单和清单，不只改某一个 agent 的口头目标。P1 优先顺序按试点缺口决定；专业连接器、OCR、追问、PDF 相互没有强制实现依赖。

工时仍沿用 PRD 中“单名全职开发、43–64 人日，含缓冲约 11–16 周”的条件化估算；工单数量和并行 agent 数不能直接用于换算完工日期。MI-001/002 完成后再按实测复杂度细化排期。

## 3. 工单清单

| 工单 | 交付主题 | 优先级 | 对应阶段 | 硬前置 |
| --- | --- | --- | --- | --- |
| [MI-001](./MI-001-baseline-fixtures.md) | 建立开发基线与离线业务样例 | P0 | M0 | 无，可开始 |
| [MI-002](./MI-002-contracts.md) | 冻结证据模型、状态与模块接口 | P0 | M0 | 无，可开始 |
| [MI-003](./MI-003-storage-versions.md) | 实现证据存储、版本和审计基础 | P0 | M1 | [MI-002](./MI-002-contracts.md) |
| [MI-004](./MI-004-task-api-auth.md) | 实现任务 API、身份与资源访问控制 | P0 | M1 | [MI-003](./MI-003-storage-versions.md) |
| [MI-005](./MI-005-runtime-budget.md) | 实现持久任务执行、预算、取消和恢复 | P0 | M1 | [MI-003](./MI-003-storage-versions.md) |
| [MI-006](./MI-006-html-pdf-evidence.md) | 实现 HTML 与文本 PDF 的证据采集 | P0 | M1 | [MI-005](./MI-005-runtime-budget.md) |
| [MI-007](./MI-007-claim-extraction.md) | 实现主张抽取与指标口径标准化 | P0 | M1 | [MI-001](./MI-001-baseline-fixtures.md)、[MI-006](./MI-006-html-pdf-evidence.md) |
| [MI-008](./MI-008-source-policy.md) | 实现发布主体目录与来源评级规则 | P0 | M1 | [MI-003](./MI-003-storage-versions.md) |
| [MI-009](./MI-009-single-source-milestone.md) | 实现单证据采纳与最小可演示闭环 | P0 | M1 | [MI-004](./MI-004-task-api-auth.md)、[MI-005](./MI-005-runtime-budget.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-008](./MI-008-source-policy.md) |
| [MI-010](./MI-010-research-search-plan.md) | 实现研究范围规划与分层搜索 | P0 | M2 | [MI-004](./MI-004-task-api-auth.md)、[MI-006](./MI-006-html-pdf-evidence.md)、[MI-008](./MI-008-source-policy.md) |
| [MI-011](./MI-011-tables-attachments.md) | 补齐 CSV/XLSX 与附件表格证据 | P0 | M2 | [MI-006](./MI-006-html-pdf-evidence.md) |
| [MI-012](./MI-012-origin-independence.md) | 识别原始出处、转载与独立证据族 | P0 | M3 | [MI-007](./MI-007-claim-extraction.md)、[MI-008](./MI-008-source-policy.md) |
| [MI-013](./MI-013-cross-verification.md) | 实现交叉核验、口径冲突与版本优先 | P0 | M3 | [MI-009](./MI-009-single-source-milestone.md)、[MI-012](./MI-012-origin-independence.md) |
| [MI-014](./MI-014-research-verification-loop.md) | 集成覆盖补搜、停止条件与完整证据流程 | P0 | M3 | [MI-010](./MI-010-research-search-plan.md)、[MI-011](./MI-011-tables-attachments.md)、[MI-013](./MI-013-cross-verification.md) |
| [MI-015](./MI-015-review-invalidation.md) | 实现人工复核、修订与影响传播 | P0 | M3 | [MI-004](./MI-004-task-api-auth.md)、[MI-013](./MI-013-cross-verification.md) |
| [MI-016](./MI-016-analysis-charts.md) | 实现可复算指标、分析结果与基础图表 | P0 | M4 | [MI-013](./MI-013-cross-verification.md) |
| [MI-017](./MI-017-controlled-report.md) | 实现基于冻结证据的市场洞察报告生成 | P0 | M4 | [MI-009](./MI-009-single-source-milestone.md)、[MI-016](./MI-016-analysis-charts.md) |
| [MI-018](./MI-018-report-validation-publish.md) | 实现全文核验与报告发布准入 | P0 | M4 | [MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-017](./MI-017-controlled-report.md) |
| [MI-019](./MI-019-task-ui.md) | 实现研究创建、进度和服务端历史界面 | P0 | M1 起、M4 收口 | [MI-009](./MI-009-single-source-milestone.md) |
| [MI-020](./MI-020-evidence-review-ui.md) | 实现证据台账、原文侧栏和复核界面 | P0 | M4 | [MI-015](./MI-015-review-invalidation.md)、[MI-019](./MI-019-task-ui.md) |
| [MI-021](./MI-021-report-ui-chat.md) | 实现报告阅读与限定证据范围的追问 | P0 | M4 | [MI-018](./MI-018-report-validation-publish.md)、[MI-019](./MI-019-task-ui.md) |
| [MI-022](./MI-022-exports.md) | 实现报告与证据表导出 | P0 | M4 | [MI-018](./MI-018-report-validation-publish.md) |
| [MI-023](./MI-023-integration-operations.md) | 完成系统集成、恢复与运行加固 | P0 | M5 | [MI-020](./MI-020-evidence-review-ui.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-022](./MI-022-exports.md) |
| [MI-024](./MI-024-business-acceptance.md) | 完成业务质量评测与首期验收交付 | P0 | M5 | [MI-001](./MI-001-baseline-fixtures.md)、[MI-023](./MI-023-integration-operations.md) |
| [MI-025](./MI-025-ocr-complex-tables.md) | 扩展扫描件 OCR 与复杂表格 | P1 | 后续版本 | [MI-024](./MI-024-business-acceptance.md) |
| [MI-026](./MI-026-specialist-connector.md) | 接入一个授权专业数据源 | P1 | 后续版本 | [MI-024](./MI-024-business-acceptance.md) |
| [MI-027](./MI-027-verified-followup-chat.md) | 实现补搜后核验的报告追问 | P1 | 后续版本 | [MI-024](./MI-024-business-acceptance.md) |
| [MI-028](./MI-028-pdf-branding.md) | 实现 PDF 与品牌报告模板 | P1 | 后续版本 | [MI-024](./MI-024-business-acceptance.md) |
| [MI-029](./MI-029-monitoring-changes.md) | 实现持续跟踪、增量更新与变更提醒 | P2 | 规模化版本 | [MI-024](./MI-024-business-acceptance.md) |

## 4. 必须通过的集成节点

| 节点 | 负责工单 | 演示/验收结果 |
| --- | --- | --- |
| 契约冻结 | [MI-002](./MI-002-contracts.md) | 模型、状态、API、模块输入输出可被各 agent 共同使用 |
| M1 最小证据闭环 | [MI-009](./MI-009-single-source-milestone.md) | 提交限定任务，拿到可定位的 HTML/PDF 数字、采纳理由及错误拦截 |
| M2/M3 完整证据流程 | [MI-014](./MI-014-research-verification-loop.md) | 多类型采集、来源分级、独立核验、补搜及停止全部通过同一链 |
| M4 完整后端 | [MI-018](./MI-018-report-validation-publish.md) | 已核验证据→计算→写作→全文检查→正式/部分报告，错误不可发布 |
| M5 完整应用 | [MI-023](./MI-023-integration-operations.md) | 创建、进度、证据、复核、报告、追问、导出，以及中断恢复 |
| P0 业务验收 | [MI-024](./MI-024-business-acceptance.md) | T01–T22、冻结真实测试集、质量/覆盖/人工耗时和费用对照 |

子工单交付独立 router/面板时，需提供注册说明，由对应集成工单接入共享入口。这些集成节点必须在实际应用路径上运行；“全部单元测试通过但模块未串联”不能作为阶段完成。

## 5. 行业未定时如何推进

- MI-001 可先建立明确标记的合成工程样例和通用数据格式；MI-002–MI-023 的通用实现可据此推进。
- 真实试点行业、生产来源目录、业务金标准、认可的评级政策及实际 provider/预算，在基线记录中列为外部输入，不由 agent 伪造。
- 当前 PRD 的中文/国内市场是可调整默认值，不是已确认的行业或地区边界。
- MI-024 可以先交付 runner 和工程结果；缺少真实数据/业务人员评价时，最终业务验收为 `pending_business`，不能声称已验证准确率和节省人工时间。
- 扫描件、商业数据库、自动补搜追问、PDF 品牌模板和监控已有后续工单，不能为了省拆单而混入 P0。

## 6. 需求覆盖

| PRD 功能 | 对应工单 |
| --- | --- |
| F01 | [MI-002](./MI-002-contracts.md)、[MI-004](./MI-004-task-api-auth.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-019](./MI-019-task-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| F02 | [MI-005](./MI-005-runtime-budget.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-023](./MI-023-integration-operations.md) |
| F03 | [MI-002](./MI-002-contracts.md)、[MI-003](./MI-003-storage-versions.md)、[MI-006](./MI-006-html-pdf-evidence.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-011](./MI-011-tables-attachments.md)、[MI-023](./MI-023-integration-operations.md) |
| F04 | [MI-002](./MI-002-contracts.md)、[MI-008](./MI-008-source-policy.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| F05 | [MI-002](./MI-002-contracts.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-011](./MI-011-tables-attachments.md)、[MI-023](./MI-023-integration-operations.md) |
| F06 | [MI-002](./MI-002-contracts.md)、[MI-012](./MI-012-origin-independence.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-023](./MI-023-integration-operations.md) |
| F07 | [MI-002](./MI-002-contracts.md)、[MI-003](./MI-003-storage-versions.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| F08 | [MI-002](./MI-002-contracts.md)、[MI-016](./MI-016-analysis-charts.md)、[MI-023](./MI-023-integration-operations.md) |
| F09 | [MI-002](./MI-002-contracts.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-017](./MI-017-controlled-report.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-023](./MI-023-integration-operations.md) |
| F10 | [MI-004](./MI-004-task-api-auth.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-019](./MI-019-task-ui.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-022](./MI-022-exports.md)、[MI-023](./MI-023-integration-operations.md) |
| F11 | [MI-002](./MI-002-contracts.md)、[MI-003](./MI-003-storage-versions.md)、[MI-004](./MI-004-task-api-auth.md)、[MI-005](./MI-005-runtime-budget.md)、[MI-008](./MI-008-source-policy.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-019](./MI-019-task-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| F12 | [MI-001](./MI-001-baseline-fixtures.md)、[MI-024](./MI-024-business-acceptance.md) |
| F13 | [MI-025](./MI-025-ocr-complex-tables.md) |
| F14 | [MI-026](./MI-026-specialist-connector.md) |
| F15 | [MI-027](./MI-027-verified-followup-chat.md)、[MI-028](./MI-028-pdf-branding.md) |
| F16 | [MI-029](./MI-029-monitoring-changes.md) |

## 7. 验收用例覆盖

每条用例由对应实现工单验证；MI-024 对 T01–T22 全量验收。表内列出 P0 的直接责任，P1/P2 还须验证其影响的相关回归。

| PRD 用例 | 直接实现/集成工单 |
| --- | --- |
| T01 | [MI-008](./MI-008-source-policy.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-017](./MI-017-controlled-report.md) |
| T02 | [MI-008](./MI-008-source-policy.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-013](./MI-013-cross-verification.md) |
| T03 | [MI-012](./MI-012-origin-independence.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md) |
| T04 | [MI-012](./MI-012-origin-independence.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-020](./MI-020-evidence-review-ui.md) |
| T05 | [MI-008](./MI-008-source-policy.md)、[MI-012](./MI-012-origin-independence.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md) |
| T06 | [MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-017](./MI-017-controlled-report.md)、[MI-020](./MI-020-evidence-review-ui.md) |
| T07 | [MI-007](./MI-007-claim-extraction.md)、[MI-013](./MI-013-cross-verification.md)、[MI-016](./MI-016-analysis-charts.md) |
| T08 | [MI-007](./MI-007-claim-extraction.md)、[MI-008](./MI-008-source-policy.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-013](./MI-013-cross-verification.md) |
| T09 | [MI-006](./MI-006-html-pdf-evidence.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-011](./MI-011-tables-attachments.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-022](./MI-022-exports.md) |
| T10 | [MI-006](./MI-006-html-pdf-evidence.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-011](./MI-011-tables-attachments.md)、[MI-023](./MI-023-integration-operations.md) |
| T11 | [MI-006](./MI-006-html-pdf-evidence.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-011](./MI-011-tables-attachments.md) |
| T12 | [MI-009](./MI-009-single-source-milestone.md)、[MI-017](./MI-017-controlled-report.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-022](./MI-022-exports.md) |
| T13 | [MI-016](./MI-016-analysis-charts.md)、[MI-017](./MI-017-controlled-report.md) |
| T14 | [MI-005](./MI-005-runtime-budget.md)、[MI-009](./MI-009-single-source-milestone.md)、[MI-013](./MI-013-cross-verification.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-023](./MI-023-integration-operations.md) |
| T15 | [MI-003](./MI-003-storage-versions.md)、[MI-005](./MI-005-runtime-budget.md)、[MI-019](./MI-019-task-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| T16 | [MI-005](./MI-005-runtime-budget.md)、[MI-014](./MI-014-research-verification-loop.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-019](./MI-019-task-ui.md)、[MI-023](./MI-023-integration-operations.md) |
| T17 | [MI-004](./MI-004-task-api-auth.md)、[MI-007](./MI-007-claim-extraction.md)、[MI-010](./MI-010-research-search-plan.md)、[MI-019](./MI-019-task-ui.md) |
| T18 | [MI-006](./MI-006-html-pdf-evidence.md)、[MI-008](./MI-008-source-policy.md)、[MI-023](./MI-023-integration-operations.md) |
| T19 | [MI-021](./MI-021-report-ui-chat.md) |
| T20 | [MI-003](./MI-003-storage-versions.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-018](./MI-018-report-validation-publish.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-023](./MI-023-integration-operations.md) |
| T21 | [MI-004](./MI-004-task-api-auth.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-020](./MI-020-evidence-review-ui.md)、[MI-021](./MI-021-report-ui-chat.md)、[MI-022](./MI-022-exports.md)、[MI-023](./MI-023-integration-operations.md) |
| T22 | [MI-018](./MI-018-report-validation-publish.md)、[MI-022](./MI-022-exports.md) |

## 8. 完成状态与派单记录

清单初始状态均为 `planned`。建议在实际派发时记录负责 agent/分支、开始时间、前置提交、产物链接与验收结果。允许的管理状态：

- `planned`：尚未派发。
- `in_progress`：正在实施。
- `blocked_dependency`：前置实现缺失或不兼容；列出具体接口/文件/版本，继续本单不依赖部分。
- `ready_for_integration`：组件验收已通过，正在等待指定集成工单挂载。
- `done`：工单约定交付已验收；组件工单的 done 不等于全产品通过。
- `pending_business`：工程产物已完成，但真实业务输入或人工确认尚缺。
- `deferred`：后续优先级，暂未进入执行范围。

不要因演示成功、没有报错或时间耗尽就将工单设为 done。首期整体完成以 MI-024 的验收结论为准。
