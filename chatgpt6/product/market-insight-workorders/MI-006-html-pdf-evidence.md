# MI-006 · 实现 HTML 与文本 PDF 的证据采集

| 项目 | 内容 |
| --- | --- |
| 优先级 / 阶段 | P0 / M1 |
| 初始状态 | planned，待执行 |
| 建议负责方向 | 证据采集 |
| 硬前置 | [MI-005](./MI-005-runtime-budget.md) |
| 拓扑批次 | W3，不是日历周数 |
| 需求映射 | F03 |
| PRD 验收场景 | T09、T10、T11、T18 |
| 集成责任 | [MI-009](./MI-009-single-source-milestone.md) |

## 1. 目标

从网页或 PDF 获取可定位的原文版本，覆盖普通抓取与预取全文两条路径，供后续逐条核验。

先阅读 [统一约定](./COMMON.md)、[执行总览](./README.md) 和 [PRD](../market-insight-agent-prd.md) §3 C02–C07、§7.3、§8、§10。工单中的文件范围相对于仓库根目录；新增路径尚待实现。前置产物须已合入当前执行分支，不能另造占位依赖后宣称完成。

## 2. 当前代码入口

- [抓取管理](../../../gpt_researcher/skills/browser.py)
- [抓取结果校验](../../../gpt_researcher/scraper/scraper.py)
- [PDF 提取](../../../gpt_researcher/scraper/pymupdf/pymupdf.py)
- [URL 安全工具](../../../gpt_researcher/utils/url_security.py)

这些文件是阅读与复用入口，不表示都必须修改。优先在本单责任范围实现，通过契约接入现有模块。

## 3. 输入与交付物

**输入**

- MI-002 采集协议、MI-003 存储、MI-005 BudgetGuard；MI-001 样例可使用已合入版本。

**交付物**

- 统一 SourceDocument/EvidenceSpan、授权上传/附件入口 router、结构化采集失败记录。

## 4. 实现范围

1. 复用现有 scraper 能力，通过市场洞察适配器保存原文后再筛选；任何预取全文分支都生成相同证据对象，摘要不伪装为全文。
2. 记录请求/最终 URL、父附件页、标题、时间及其依据、材料类型、哈希、解析器版本、原文存储形式和错误状态。
3. HTML 保存标题路径和稳定文本位置；保留表头、单位、行列及脚注。PDF 保存文件页序、可得印刷页码及页内片段位置。
4. 扫描页、错位表格、登录/验证码/错误页明确报解析不足；不调用模型猜测缺失的原文。
5. 检查初始 URL、重定向与最终目标，限定上传类型/大小；局部文件通过受控上传对象读，不允许用户任意指定服务器路径。
6. 提供文档注册、采集/读取服务与受保护的上传子路由，交由 MI-009 注册；不改变普通模式原有输出格式。

## 5. 文件责任范围

- `gpt_researcher/market_insight/collectors/base.py`
- `gpt_researcher/market_insight/collectors/web.py`
- `gpt_researcher/market_insight/collectors/pdf.py`
- `backend/server/insight/documents.py`
- `tests/market_insight/test_collectors.py`

共享入口、模型定义和依赖清单遵循 COMMON 的单一写入责任。需要增加公共依赖或改契约时，在交接中列出最小变更及影响；与同批工单存在冲突的文件不得无协调并行修改。

## 6. 验收条件

- [ ] 一个 HTML 数字和一个 PDF 第 12 页表格数字均能回到保存的原文，并显示表头、单位和脚注。
- [ ] 重复采集不造成重复证据，同 URL 内容修改得到新版本；后续旧引用仍可打开。
- [ ] 长搜索摘要、验证码页、扫描件不会变成可用事实证据。
- [ ] 普通抓取与预取全文产生相同协议；无法证明元数据的字段保持未知。
- [ ] SSRF/越权文件路径及恶意 HTML 不改变系统行为；所有外部调用经过预算边界。

## 7. 验证与交接

- 基于离线 HTTP/重定向模拟、HTML/PDF 固定样例验证定位、版本、失败状态与 URL/上传边界。

按 [统一交接模板](./COMMON.md) 报告实际运行命令、结果、未测项、接口变化和集成说明。组件尚未挂入主应用时，明确 `ready_for_integration` 及接入工单，不将模拟结果冒充真实端到端能力。

## 8. 本单边界

不做扫描件 OCR，不在采集器内决定来源等级或采纳事实，不丢弃未知字段以伪造完整记录。

## 9. 可直接交给 agent 的执行指令

```text
请在 gpt-researcher 仓库执行 docs/product/market-insight-workorders/MI-006-html-pdf-evidence.md。
先读取该工单、COMMON.md 和工单指定的 PRD 段落，检查硬前置产物已在当前工作目录可用。
按本单范围完成实现、必要验证和交接；复用冻结契约，保留其他人的修改。
不要以空实现、模拟成功或跳过失败核验来完成验收。
如前置/真实业务输入缺失，列出具体缺口并推进独立部分，未满足项不得标为通过。
最终按 COMMON.md 的模板报告实际结果及交给集成工单的事项。
```
