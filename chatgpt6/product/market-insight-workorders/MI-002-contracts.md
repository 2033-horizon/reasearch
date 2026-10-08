# MI-002 · 冻结证据模型、状态与模块接口

| 项目 | 内容 |
| --- | --- |
| 优先级 / 阶段 | P0 / M0 |
| 初始状态 | planned，待执行 |
| 建议负责方向 | 核心契约 |
| 硬前置 | 无，可开始 |
| 拓扑批次 | W0，不是日历周数 |
| 需求映射 | F01、F03、F04、F05、F06、F07、F08、F09、F11 |
| PRD 验收场景 | 本单为基线/契约基础，按下述完成条件验收 |
| 集成责任 | [MI-009](./MI-009-single-source-milestone.md) |

## 1. 目标

让所有后续 agent 使用同一套 ID、数据结构、状态和错误语义；先完成可导入的契约，再允许并行实现。

先阅读 [统一约定](./COMMON.md)、[执行总览](./README.md) 和 [PRD](../market-insight-agent-prd.md) §5、§6、§8、§9.3。工单中的文件范围相对于仓库根目录；新增路径尚待实现。前置产物须已合入当前执行分支，不能另造占位依赖后宣称完成。

## 2. 当前代码入口

- [研究入口](../../../gpt_researcher/agent.py)
- [当前 API 模型](../../../backend/server/app.py)
- [现有前端数据类型](../../../frontend/nextjs/types/data.ts)

这些文件是阅读与复用入口，不表示都必须修改。优先在本单责任范围实现，通过契约接入现有模块。

## 3. 输入与交付物

**输入**

- PRD 数据对象、状态、原因码与 API 草案；本单不依赖真实数据或模型。

**交付物**

- Pydantic 模型、端口协议、兼容 TypeScript 类型/JSON Schema、版本化契约说明及示例载荷。

## 4. 实现范围

1. 实现 ResearchTask、Publisher、SourceDocument、SourceAssessment、EvidenceSpan、Claim、ClaimEvidence、OriginRelation、VerificationResult、AnalysisResult、ReportVersion、TaskEvent/AuditEvent 的最小稳定模型。
2. 区分原始材料版本、主张版本、核验记录和报告冻结版本；任务/工作空间/主体 ID 不使用显示名或 URL 代替。
3. 金额与数值采用可保持精度的表示；定义区间、倍率、百分比/百分点、币种、期间、原文定位和未知值，明确序列化方式。
4. 定义任务与主张状态、允许转移、全部 PRD 原因码；错误/超限不能转为 accepted。定义草稿与已验证报告的区别。
5. 定义 Repository、Collector、Extractor、SourcePolicy、Verifier、SearchPlanner、BudgetGuard、ReportWriter/Validator 等必要端口以及证据包、补搜请求、覆盖快照与事件载荷；只定义真实需要的边界。
6. 冻结 /api/insight 的请求/响应、分页、鉴权上下文、错误格式、幂等键和事件序号约定；实现方命名允许调整，但字段语义必须一致。
7. 输出契约版本与变更规则；后续 agent 扩展字段须保留兼容性并更新合同，不各自重造 schemas。

## 5. 文件责任范围

- `gpt_researcher/market_insight/__init__.py`
- `gpt_researcher/market_insight/schemas.py`
- `gpt_researcher/market_insight/ports.py`
- `docs/product/market-insight-contracts.md`
- `frontend/nextjs/types/market-insight.ts`
- `tests/market_insight/test_contracts.py`

共享入口、模型定义和依赖清单遵循 COMMON 的单一写入责任。需要增加公共依赖或改契约时，在交接中列出最小变更及影响；与同批工单存在冲突的文件不得无协调并行修改。

## 6. 验收条件

- [ ] Python 模型可序列化/反序列化，前后端数值与日期语义一致；未知不变成 0。
- [ ] 不能构造缺少对应证据版本的正式引用；复核动作不能由客户端直接指定 accepted 状态。
- [ ] 任务配置、源等级和采纳结论是不同字段；草稿不会默认显示为已核验报告。
- [ ] 契约涵盖 PDF 页码、HTML 文本位置、表格行列/脚注以及独立性未知，后续文件类型无需另造证据模型。
- [ ] 契约文档明确所有端口输入、输出、异常和责任人，后续工单可据此实现。

## 7. 验证与交接

- 对关键正反例进行模型校验、JSON 往返、状态转移及类型检查。
- 这些测试验证业务约束，不为每个无约束字段编写机械 getter/setter 测试。

按 [统一交接模板](./COMMON.md) 报告实际运行命令、结果、未测项、接口变化和集成说明。组件尚未挂入主应用时，明确 `ready_for_integration` 及接入工单，不将模拟结果冒充真实端到端能力。

## 8. 本单边界

不实现数据库、真实模型调用、检索、UI 或完整工作流；不引入与首期无关的框架。

## 9. 可直接交给 agent 的执行指令

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-002-contracts.md。
先读取该工单、COMMON.md 和工单指定的 PRD 段落，检查硬前置产物已在当前工作目录可用。
按本单范围完成实现、必要验证和交接；复用冻结契约，保留其他人的修改。
不要以空实现、模拟成功或跳过失败核验来完成验收。
如前置/真实业务输入缺失，列出具体缺口并推进独立部分，未满足项不得标为通过。
最终按 COMMON.md 的模板报告实际结果及交给集成工单的事项。
```
