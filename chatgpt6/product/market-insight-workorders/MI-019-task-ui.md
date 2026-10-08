# MI-019 · 实现研究创建、进度和服务端历史界面

| 项目 | 内容 |
| --- | --- |
| 优先级 / 阶段 | P0 / M1 起、M4 收口 |
| 初始状态 | planned，待执行 |
| 建议负责方向 | 前端任务界面 |
| 硬前置 | [MI-009](./MI-009-single-source-milestone.md) |
| 拓扑批次 | W6，不是日历周数 |
| 需求映射 | F01、F10、F11 |
| PRD 验收场景 | T15、T16、T17 |
| 集成责任 | [MI-023](./MI-023-integration-operations.md) |

## 1. 目标

让用户能创建范围明确的任务，看到真实阶段、预算及缺口，刷新后仍可找到任务。

先阅读 [统一约定](./COMMON.md)、[执行总览](./README.md) 和 [PRD](../market-insight-agent-prd.md) §4、§7.2、§7.7、§7.8。工单中的文件范围相对于仓库根目录；新增路径尚待实现。前置产物须已合入当前执行分支，不能另造占位依赖后宣称完成。

## 2. 当前代码入口

- [现有研究表单](../../../frontend/nextjs/components/Task/ResearchForm.tsx)
- [现有连接 hook](../../../frontend/nextjs/hooks/useWebSocket.ts)
- [现有历史 hook](../../../frontend/nextjs/hooks/useResearchHistory.ts)

这些文件是阅读与复用入口，不表示都必须修改。优先在本单责任范围实现，通过契约接入现有模块。

## 3. 输入与交付物

**输入**

- MI-002 前端/API 契约、任务与事件服务；可使用契约一致的 fixture 开发视图。

**交付物**

- 新建/任务壳/历史界面、通用认证请求与事件读取、子面板组合接口。

## 4. 实现范围

1. 独立增加市场洞察入口，提供目的、对象/别名、地区/期间/截止日、必答指标、语言及预算；展示未确定假设，行业模板保持可配置。
2. 显示消歧与缺失字段反馈，不偷偷提交默认企业；限制与后端一致。
3. 按服务端阶段/事件显示材料数、证据数、独立来源、覆盖、费用与未计价项，不能用伪进度到 100% 代表完成。
4. 提供取消、恢复/重试和断线续读；错误、needs_review、completed_with_gaps、failed 各自有明确可操作状态。
5. 历史由服务端获取，允许跨浏览器授权查看；localStorage 只作缓存，不作权限或唯一记录来源。
6. 为证据、复核和报告面板提供稳定 props/事件插槽，后续 agent 不改本单的公共请求/状态定义。

## 5. 文件责任范围

- `frontend/nextjs/app/insight/page.tsx`
- `frontend/nextjs/app/insight/[id]/page.tsx 的任务壳`
- `frontend/nextjs/components/insight/TaskForm.tsx`
- `frontend/nextjs/components/insight/TaskShell.tsx`
- `frontend/nextjs/components/insight/TaskProgress.tsx`
- `frontend/nextjs/hooks/useInsightTask.ts`
- `frontend/nextjs/lib/insight-client.ts`

共享入口、模型定义和依赖清单遵循 COMMON 的单一写入责任。需要增加公共依赖或改契约时，在交接中列出最小变更及影响；与同批工单存在冲突的文件不得无协调并行修改。

## 6. 验收条件

- [ ] 用户能提交/查看/取消任务，企业歧义与范围默认值清晰可见。
- [ ] 刷新/断开连接后恢复同一个任务，历史不依赖旧 localStorage ID。
- [ ] 部分完成和待复核不展示成成功完整报告，未计价不显示 0 元。
- [ ] 预算/语言等前后端一致，3 个任务的事件不会串到另一任务页面。
- [ ] 真实任务 API 的基本流程可用；子面板尚未接入时不展示虚假成功内容。

## 7. 验证与交接

- 执行前端类型检查/现有构建命令，并验证创建、断线恢复、错误和历史场景；组件测试或交互检查按项目既有工具选择。

按 [统一交接模板](./COMMON.md) 报告实际运行命令、结果、未测项、接口变化和集成说明。组件尚未挂入主应用时，明确 `ready_for_integration` 及接入工单，不将模拟结果冒充真实端到端能力。

## 8. 本单边界

不复制后端核验规则到前端，不用前端本地状态生成已采纳标记；完整面板集成归 MI-023。

## 9. 可直接交给 agent 的执行指令

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-019-task-ui.md。
先读取该工单、COMMON.md 和工单指定的 PRD 段落，检查硬前置产物已在当前工作目录可用。
按本单范围完成实现、必要验证和交接；复用冻结契约，保留其他人的修改。
不要以空实现、模拟成功或跳过失败核验来完成验收。
如前置/真实业务输入缺失，列出具体缺口并推进独立部分，未满足项不得标为通过。
最终按 COMMON.md 的模板报告实际结果及交给集成工单的事项。
```
