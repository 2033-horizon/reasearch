# MI-020 · 实现证据台账、原文侧栏和复核界面

| 项目 | 内容 |
| --- | --- |
| 优先级 / 阶段 | P0 / M4 |
| 初始状态 | planned，待执行 |
| 建议负责方向 | 前端证据与复核 |
| 硬前置 | [MI-015](./MI-015-review-invalidation.md)、[MI-019](./MI-019-task-ui.md) |
| 拓扑批次 | W8，不是日历周数 |
| 需求映射 | F04、F07、F10 |
| PRD 验收场景 | T04、T06、T09、T20、T21 |
| 集成责任 | [MI-023](./MI-023-integration-operations.md) |

## 1. 目标

让领导一键查看数据依据，让研究人员集中处理高影响例外，无需逐条重新搜索。

先阅读 [统一约定](./COMMON.md)、[执行总览](./README.md) 和 [PRD](../market-insight-agent-prd.md) §4、§7.4、§7.7。工单中的文件范围相对于仓库根目录；新增路径尚待实现。前置产物须已合入当前执行分支，不能另造占位依赖后宣称完成。

## 2. 当前代码入口

- [当前来源卡片](../../../frontend/nextjs/components/ResearchBlocks/elements/SourceCard.tsx)
- [当前来源列表](../../../frontend/nextjs/components/ResearchBlocks/Sources.tsx)

这些文件是阅读与复用入口，不表示都必须修改。优先在本单责任范围实现，通过契约接入现有模块。

## 3. 输入与交付物

**输入**

- 主张/证据/复核 API、MI-019 通用请求与面板接口，MI-002 类型。

**交付物**

- 可筛选证据面板、原文定位视图、冲突对照和有审计的复核交互。

## 4. 实现范围

1. 展示指标、主体、原始值/归一值、期间/单位、来源类型/等级、采纳状态及独立证据数；提供筛选和分页。
2. 从证据 ID 展开原文，显示高亮、页码/行列、表头脚注、原始链接、版本和评级理由；无法加载时显示真实原因。
3. 将 same_origin 与独立性未知清楚展示，不能把普通 source count 标成独立支持数。
4. 按后端队列显示争议和缺口，提供并排原文、补材料、更正、拒绝与重新核验动作。
5. 复核必须填理由并提交期望版本；过期冲突提示刷新；普通读者只读，不显示可绕过权限的强行批准。
6. 安全展示原文文本/受控 HTML，未经处理的页面脚本不得在侧栏执行；输出面板组件供 MI-023 组合。

## 5. 文件责任范围

- `frontend/nextjs/components/insight/EvidenceTable.tsx`
- `frontend/nextjs/components/insight/EvidenceDrawer.tsx`
- `frontend/nextjs/components/insight/ReviewQueue.tsx`
- `frontend/nextjs/components/insight/ReviewForm.tsx`
- `frontend/nextjs/lib/insight-evidence.ts`

共享入口、模型定义和依赖清单遵循 COMMON 的单一写入责任。需要增加公共依赖或改契约时，在交接中列出最小变更及影响；与同批工单存在冲突的文件不得无协调并行修改。

## 6. 验收条件

- [ ] PDF 和表格证据位置可见且与后端记录一致；报告可通过 onSelectEvidence 打开对应片段。
- [ ] 多站转载显示为一个原始族；来源 A 不等同于已采纳。
- [ ] 无依据不能点击变成 verified，更正后 UI 显示需重核验及受影响报告。
- [ ] 未授权用户无法操作复核或通过网络请求读取证据；原文恶意 HTML 不执行。
- [ ] 接口失败、空列表、长期待复核和版本冲突都有可理解的界面状态。

## 7. 验证与交接

- 类型/构建检查，覆盖 1 条 A、同源多站、可比冲突和复核版本冲突的交互；用真实接口联调。

按 [统一交接模板](./COMMON.md) 报告实际运行命令、结果、未测项、接口变化和集成说明。组件尚未挂入主应用时，明确 `ready_for_integration` 及接入工单，不将模拟结果冒充真实端到端能力。

## 8. 本单边界

不自行重新评级或计算采纳结论，不实现复杂来源管理后台；简单目录导入已在 MI-008。

## 9. 可直接交给 agent 的执行指令

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-020-evidence-review-ui.md。
先读取该工单、COMMON.md 和工单指定的 PRD 段落，检查硬前置产物已在当前工作目录可用。
按本单范围完成实现、必要验证和交接；复用冻结契约，保留其他人的修改。
不要以空实现、模拟成功或跳过失败核验来完成验收。
如前置/真实业务输入缺失，列出具体缺口并推进独立部分，未满足项不得标为通过。
最终按 COMMON.md 的模板报告实际结果及交给集成工单的事项。
```
