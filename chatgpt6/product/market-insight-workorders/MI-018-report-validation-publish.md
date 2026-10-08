# MI-018 · 实现全文核验与报告发布准入

| 项目 | 内容 |
| --- | --- |
| 优先级 / 阶段 | P0 / M4 |
| 初始状态 | planned，待执行 |
| 建议负责方向 | 报告质量与集成 |
| 硬前置 | [MI-014](./MI-014-research-verification-loop.md)、[MI-015](./MI-015-review-invalidation.md)、[MI-017](./MI-017-controlled-report.md) |
| 拓扑批次 | W9，不是日历周数 |
| 需求映射 | F07、F09、F11 |
| PRD 验收场景 | T12、T14、T16、T20、T22 |
| 集成责任 | [MI-018](./MI-018-report-validation-publish.md) |

## 1. 目标

把报告从草稿到合格版本的转换放在可审计的核验流程中，并接通完整后端研究链。

先阅读 [统一约定](./COMMON.md)、[执行总览](./README.md) 和 [PRD](../market-insight-agent-prd.md) §5、§7.6、§7.8、§12。工单中的文件范围相对于仓库根目录；新增路径尚待实现。前置产物须已合入当前执行分支，不能另造占位依赖后宣称完成。

## 2. 当前代码入口

- [现有引用处理](../../../gpt_researcher/actions/markdown_processing.py)
- [现有质量指标](../../../evals/quality_eval/metrics.py)
- [多智能体放行分支](../../../multi_agents/agents/orchestrator.py)

这些文件是阅读与复用入口，不表示都必须修改。优先在本单责任范围实现，通过契约接入现有模块。

## 3. 输入与交付物

**输入**

- MI-014 证据快照、MI-015 修订/失效状态、MI-017 草稿及引用映射。

**交付物**

- 逐项质量结果、失败位置与修订请求；冻结报告版本和 completed/completed_with_gaps/needs_review 状态。

## 4. 实现范围

1. 检查全文摘要、段落、表格、图表及附录的事实覆盖；包括没有引用 ID 的新主张，而不是只验证模型主动标出来的引用。
2. 程序检验 ID 属于本任务/版本/授权范围，原文定位有效，数值/单位/期间/主体匹配，派生值可重算。
3. 结合原文与限定语做语义支持检查，拆分复合句；模型检查失败是未通过，不以未返回错误当作成功。
4. 核对必答指标覆盖、预测标签、争议与假设；有缺口但事实均合格时可部分完成，有错引/假数据时不能当作合格部分报告。
5. 实现有预算的有限修订；达到上限后 needs_review，禁用 force-accept；给 MI-015 的修订动作保留局部重核验入口。
6. 在一次受控发布中冻结证据/规则/模板与报告版本；挂载报告和复核 API，接通研究→分析→写作→验证主链。

## 5. 文件责任范围

- `gpt_researcher/market_insight/report_validator.py`
- `gpt_researcher/market_insight/publisher.py`
- `gpt_researcher/market_insight/workflow.py 的分析/写作/发布组合`
- `backend/server/insight/reports.py`
- `backend/server/insight_routes.py 的复核/报告注册`
- `tests/market_insight/test_report_gate.py`

共享入口、模型定义和依赖清单遵循 COMMON 的单一写入责任。需要增加公共依赖或改契约时，在交接中列出最小变更及影响；与同批工单存在冲突的文件不得无协调并行修改。

## 6. 验收条件

- [ ] T12/T22 的删除引用、替换来源、改单位、摘要/图表注入新数字等扰动被定位并拦截。
- [ ] 全部必答有合格答案才能 completed；仅有覆盖缺口可 completed_with_gaps，不能混淆两者。
- [ ] 修订次数耗尽、预算耗尽、模型校验异常都不绕过门槛。
- [ ] 发布前输入被修订/失效时拒绝使用过期快照；旧版本可追溯且不会被覆写。
- [ ] 通过真实后端 API 完成一份固定样例报告，能够从每个数字展开到保存的证据。

## 7. 验证与交接

- 运行完整发布正例和 T12/T14/T16/T20/T22 扰动集；检查幂等发布与并发修订竞态。
- 旧域名级 quality_eval 可作观察指标，但测试必须验证主张/原文级支持。

按 [统一交接模板](./COMMON.md) 报告实际运行命令、结果、未测项、接口变化和集成说明。组件尚未挂入主应用时，明确 `ready_for_integration` 及接入工单，不将模拟结果冒充真实端到端能力。

## 8. 本单边界

不允许只抽查前几段或前 10 个主张就称全文验证，不把排版和长篇幅当作质量通过。

## 9. 可直接交给 agent 的执行指令

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-018-report-validation-publish.md。
先读取该工单、COMMON.md 和工单指定的 PRD 段落，检查硬前置产物已在当前工作目录可用。
按本单范围完成实现、必要验证和交接；复用冻结契约，保留其他人的修改。
不要以空实现、模拟成功或跳过失败核验来完成验收。
如前置/真实业务输入缺失，列出具体缺口并推进独立部分，未满足项不得标为通过。
最终按 COMMON.md 的模板报告实际结果及交给集成工单的事项。
```
